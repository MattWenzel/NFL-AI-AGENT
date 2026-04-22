# Runtime

The runtime is the heart of the agent: one class, `ChatRuntime`, drives every user turn through the model → tool loop → persistence pipeline. It is transport-agnostic — the same runtime powers `/chat/stream`, `/chat/message`, and any future non-HTTP entry point.

This doc covers the iteration loop, the event stream, the session lock, doom-loop protection, context-overflow recovery, and cleanup on early exit. Compaction and tools have their own docs ([compaction.md](compaction.md), [tools.md](tools.md)).

## File map

The `agent/` package is split by concern — `ChatRuntime` orchestrates, but most of the actual work lives in sibling collaborators:

- `agent/runtime.py` — `ChatRuntime`: the loop itself. Thin orchestrator that wires everything else together.
- `agent/runtime_policy.py` — `RuntimeLoopState`: per-user-turn state (iteration count, tool-choice overrides, overflow-retry flag, accumulated tool runs). Doom-loop detection lives here (`raise_if_doom_loop`).
- `agent/tool_execution.py` — `ToolExecutionService`: runs each pass's tool calls under `asyncio.gather`; handles pre/post persistence.
- `agent/turn_manager.py` — `AssistantTurnManager` + `AssistantTurnContext`: per-turn lifecycle (open, record text/tool call, complete, error, cleanup) and the text coalescing buffer.
- `agent/persistence.py` — `RuntimePersistence`: write-side facade over `RuntimeStore` for hot-path ops.
- `agent/events.py` — `RuntimeEvent` dataclass + `RuntimeLoopError` exception.
- `agent/message_builder.py` — `build_model_messages`: `SessionTranscript` → wire-format `list[Message]` for the next provider call.
- `agent/system_prompt.py` — `get_base_prompt`: the slim base system prompt (rules + guide index).
- `agent/compaction.py`, `agent/summarizer.py`, `agent/token_counting.py` — see [compaction.md](compaction.md).

## `ChatRuntime`

Defined at `agent/runtime.py:48`. Constructor composes four collaborators (`runtime.py:51-59`):

```python
class ChatRuntime:
    def __init__(self, store: RuntimeStore):
        self.store = store
        self.persistence = RuntimePersistence(store)
        self.turns = AssistantTurnManager(self.persistence)
        self.tools = ToolExecutionService(
            store,
            self.persistence,
            execute_tool=lambda *a, **kw: execute_tool_structured(*a, **kw),
        )
```

`store` is the only external dependency. Everything else is a pure-Python object owned by the runtime.

Public entry points:

- `prepare_session(client, provider_name, conversation_id, user_id) -> SessionRecord` (`runtime.py:61`) — resolve or create a session. Called from the transport layer (via `ChatService.prepare_chat`) so every caller keys sessions the same way (by provider, model, context window).
- `run_session(session, user_text, client, *, tools, provider_name, tool_choice=None) -> AsyncIterator[RuntimeEvent]` (`runtime.py:78`) — drive one user turn. Always consumes `client.stream_message`; non-streaming callers buffer the events at the transport boundary.

## The iteration loop

`MAX_TOOL_ITERATIONS = 10` (`runtime.py:46`). A single `run_session` drives the model through up to 10 passes. Each pass is one `client.stream_message` call. The loop exits when the model returns a text-only response (no tool calls), when the budget is exhausted, or on an unrecoverable error.

```
run_session(user_text)
 ├─ acquire session lock                                    runtime.py:96
 ├─ persistence.create_user_turn                            runtime.py:99
 ├─ persistence.update_session_metadata (title on 1st turn) runtime.py:100
 ├─ yield turn_started                                      runtime.py:107
 ├─ RuntimeLoopState(initial_tool_choice)                   runtime.py:108
 └─ for _ in range(MAX_TOOL_ITERATIONS):                    runtime.py:110
      ├─ begin_iteration (bumps iterations, consumes forced tool_choice)
      ├─ compact_if_needed → maybe yield compaction_started runtime.py:112
      ├─ turns.open_turn → yield assistant_started          runtime.py:120
      ├─ reset client.last_usage / last_stop_reason
      ├─ transcript = await store.get_transcript(...)
      ├─ async for event in client.stream_message(...):     runtime.py:132
      │    ├─ RetryingEvent     → yield retrying
      │    ├─ TextEvent         → turns.record_text_delta   → yield text_delta
      │    └─ ToolUseEvent      → turns.record_tool_call    → yield tool_pending
      ├─ turns.complete_turn                                runtime.py:166
      │    ├─ if no tool_runs   → yield turn_finished, return
      │    └─ else              → fall through to tool dispatch
      ├─ loop_state.record_tool_runs  (also calls raise_if_doom_loop)  runtime.py:178
      ├─ tools.execute_many (asyncio.gather)                runtime.py:180
      ├─ yield tool_completed / tool_failed per result      runtime.py:185
      └─ yield assistant_requires_followup                  runtime.py:197
   (budget exhausted) → yield max_iterations_event          runtime.py:250
finally:
    if active_turn: turns.cleanup_interrupted               runtime.py:251
```

Three things worth calling out about this shape:

1. **Tools run concurrently within a pass.** `ToolExecutionService.execute_many` is `asyncio.gather` over the pass's tool runs (`tool_execution.py:39-47`). Each handler runs in `asyncio.to_thread` because the SQL sandbox is sync (see [tools.md](tools.md)).
2. **No tool calls = turn done.** The only normal exit from the loop is the model emitting a pure-text response — `turns.complete_turn` returns a `turn_finished` event in that case and the runtime returns. This is why guides, schema fetches, and SQL calls all ultimately cycle back to the model for synthesis.
3. **Runtime doesn't write to SQLite directly.** Every persistence concern goes through `self.persistence` (the write-side facade) or `self.turns` (which owns `RuntimePersistence` internally). The runtime's own code is almost entirely control flow.

## Session lock

Every turn runs under `self.store.lock(session.id)` (`runtime.py:96-97`). The lock is a per-session `asyncio.Lock` held by the store; concurrent requests to the same conversation serialize. Two reasons it matters:

- **Transcript consistency.** Turns, assistant parts, and tool runs are written in strict order, so `build_model_messages` on the next pass always sees a coherent history.
- **No double-spending.** If a user fires two messages in the same conversation at once, the second blocks until the first's loop exits.

Cross-session calls are unaffected — the lock is keyed by `session.id`, not global.

## `RuntimeLoopState`

`agent/runtime_policy.py`. The per-user-turn state container. Fields:

- `iterations: int` — loop counter.
- `initial_tool_choice`, `force_tool_choice_next_iter` — `tool_choice` override hooks (e.g., for the overflow-retry path).
- `force_overflow_compaction: bool` — set by `handle_overflow()` so the next iteration forces compaction.
- `overflow_retry_used: bool` — one-shot flag; a second overflow falls through.
- `user_turn_tool_runs: list[ToolRunRecord]` — accumulates every tool run this user turn. Resets per user message.

Methods called from the loop:

- `begin_iteration()` — bumps `iterations`, consumes any `force_tool_choice_next_iter`.
- `compact_if_needed(store, session, client, provider_name)` — wraps `agent.compaction.compact_if_needed` and plumbs through the forced-compaction flag when `force_overflow_compaction` is set. Returns a dict of compaction metadata or `None`.
- `compaction_event(session_id, meta)` — builds the `compaction_started` `RuntimeEvent`.
- `record_tool_runs(tool_runs)` — appends to `user_turn_tool_runs` and calls `raise_if_doom_loop`.
- `handle_overflow()` — first call sets the retry flag + returns `True`; second call returns `False` (caller emits `runtime_error`).
- `max_iterations_event(session_id, limit)` — builds the terminal `runtime_error` event when the loop exhausts its budget.

## `RuntimeEvent`

One dataclass, one discriminator field (`agent/events.py:14-31`). Relevant fields per event:

| Field | Used by |
|-------|---------|
| `type` | discriminator (catalog below) |
| `session_id` | always set |
| `turn_id` | turn-scoped events |
| `text` | `text_delta` |
| `tool_run_id`, `name`, `input`, `result`, `error` | tool lifecycle events |
| `iterations` | set on every turn/iteration-scoped event |
| `meta` | `compaction_started` |
| `attempt`, `delay_seconds` | `retrying` |
| `status` | reserved; unused by current emitters |

### Event catalog

| `type` | Emitted at | Payload |
|--------|-----------|---------|
| `turn_started` | after user turn persisted (`runtime.py:107`) | `turn_id` of the user turn |
| `compaction_started` | when `compact_if_needed` returns metadata (`runtime.py:118-119`) | `meta`, `turn_id` = summary turn |
| `assistant_started` | new assistant turn opened (`runtime.py:120-124`) | `turn_id`, `iterations` |
| `retrying` | provider transient error mid-stream (`runtime.py:138-149`) | `attempt`, `delay_seconds`, `error` |
| `text_delta` | per `TextEvent` from the stream (`runtime.py:151-157`) | `text`, `turn_id`, `iterations` |
| `tool_pending` | per `ToolUseEvent` (`runtime.py:158-164`) | `tool_run_id`, `name`, `input` |
| `turn_finished` | text-only response, no tool calls (`runtime.py:173-176`, via `turns.complete_turn`) | `turn_id`, `iterations` |
| `tool_completed` / `tool_failed` | after `tools.execute_many` (`runtime.py:185-195`) | `tool_run_id`, `name`, `result`, `error` |
| `assistant_requires_followup` | after tools, before next pass (`runtime.py:197-202`) | `turn_id`, `iterations` |
| `runtime_error` | overflow retry exhausted, `RuntimeLoopError`, or max iterations (`runtime.py:218`, `:234`, `:250`) | `error`, `iterations` |

Transports serialize these differently. The SSE adapter (`server/sse.py`) drops internal events like `turn_started` / `turn_finished` / `assistant_requires_followup` and renames others for the browser — see [transport.md](transport.md#sse-event-catalog).

## Turn lifecycle (`AssistantTurnManager`)

`agent/turn_manager.py`. Owns everything about one assistant turn: the `TurnRecord`, the `AssistantTextBuffer` (coalesces text chunks before persisting — streaming still emits every `text_delta`, but SQLite writes are batched at ~256 chars to reduce churn), and the per-turn `tool_runs` list.

| Method | What it does |
|--------|--------------|
| `open_turn` | Creates the assistant turn row (status=running), builds the text buffer, returns the context + an `assistant_started` event. |
| `record_text_delta` | Appends to the buffer, flushes if threshold reached, returns a `text_delta` event. |
| `record_tool_call` | Flushes the buffer, creates the tool_run + tool_call part, appends to `context.tool_runs`, returns a `tool_pending` event. |
| `complete_turn` | Flushes the buffer, marks the turn `completed`, records token usage. Returns a `turn_finished` event **only if there are no tool calls** — otherwise returns `None` so the loop continues. |
| `mark_turn_error` | Flushes the buffer, marks the turn `error`, records the error text. |
| `cleanup_interrupted` | Flushes the buffer, marks any still-running turn/tool runs `interrupted`. Called from the runtime's `finally` block. |

`TITLE_PREVIEW_CHARS = 80` (`turn_manager.py`) is the truncation length for the auto-generated session title (the first user message's first 80 characters).

## Tool execution (`ToolExecutionService`)

`agent/tool_execution.py`. Called once per pass via `self.tools.execute_many(session_id, turn, tool_runs)` (`runtime.py:180-184`):

1. `execute_many` fans out to `execute_one` per tool run under `asyncio.gather`.
2. Each `execute_one` calls `persistence.begin_tool_execution` — flips status to `running`, writes a `tool_status` part so the UI can show a spinner.
3. It then calls the injected `execute_tool` callable (wired to `execute_tool_structured` — see [tools.md](tools.md)) with the tool name, input dict, and a `ctx` dict carrying a `register_export` callback. That callback is the **side-channel** handlers use to reach persistence without importing it (`create_csv_export` uses it to index generated files in the export library).
4. Wraps the return value in a `ToolExecutionResult(status, content, error, hint, duration_ms)`.
5. Calls `persistence.complete_tool_execution` — updates status/result/error/hint/duration and writes a `tool_result` part.

Handlers never touch the store. Everything flows through `ctx` or the return envelope — this is what keeps `tools/` free of infra dependencies.

## Doom-loop detector

`runtime_policy.raise_if_doom_loop`. Called from `loop_state.record_tool_runs` (`runtime.py:178`) **before** tool dispatch for the current pass. If the tail `DOOM_LOOP_MATCH = 3` tool runs in `user_turn_tool_runs` have the same `tool_name` and the same canonical-JSON `input`, raises `RuntimeLoopError("Repeated identical tool calls detected …")`.

Why this shape: a model stuck in a loop typically repeats the *same* call over and over. Three identical calls in a row is a strong signal — healthy use varies SQL between retries, so three in a row catches failure modes before the iteration budget exhausts.

The input fingerprint is the `ToolInputJSON` column's on-disk representation — `json.dumps(input, sort_keys=True)` — so two semantically-equivalent dicts always fingerprint identically (see [persistence.md](persistence.md#file-map)).

Scope is one user turn. `user_turn_tool_runs` is cleared when the next user message arrives, so a follow-up like "try again" doesn't trip the detector on the first repeat.

## Context-overflow recovery

Providers sometimes reject a prompt our estimator was happy with — typically due to cumulative tool-result size we under-counted. `ContextOverflowError` is raised by the provider adapter (`provider/base.py`) and caught at `runtime.py:204`:

1. Mark the current assistant turn `error` with the overflow message.
2. `loop_state.handle_overflow()`:
   - If this is the first overflow this user turn: sets `force_overflow_compaction=True` and `overflow_retry_used=True`, returns `True`.
   - If it's the second: returns `False`.
3. If `True`: `continue` the loop. Next iteration's `compact_if_needed` runs with `force=True` and an aggressive `retention_budget_override = context_window // 4`, so the prompt shrinks dramatically before we re-enter the provider.
4. If `False`: yield a `runtime_error` event and return — the user sees the overflow message and can retry.

One-shot retry per user turn. No exponential backoff, no multiple compactions — if a single aggressive compaction doesn't fit, further compaction is unlikely to help.

## `RuntimeLoopError`

`events.py`. One exception for unrecoverable loop states. Raised by:

1. **Doom loop** — `raise_if_doom_loop` (above).
2. **MAX_TOKENS with no tool call** — raised inside `turns.complete_turn` when the model hit its output cap mid-text with nothing to follow up on. Surfaced as a user-facing `runtime_error` with guidance to ask a narrower question or say "continue".

Caught at `runtime.py:226-241`: the assistant turn is marked `error`, a `runtime_error` event is yielded, the loop returns cleanly. Any other exception (`runtime.py:242-249`) also marks the turn `error` but is re-raised — the runtime doesn't swallow unknown errors.

## Cleanup on early exit

The `finally` block at `runtime.py:251-253` calls `turns.cleanup_interrupted(active_turn)` when a turn is still active on the way out. Two triggers:

- The client disconnects mid-stream (FastAPI cancels the generator; see [transport.md](transport.md#producerconsumer--heartbeat)).
- An unexpected exception propagates past the loop body.

`cleanup_interrupted` flushes the text buffer and flips the assistant turn + any pending tool runs to `interrupted`.

On next startup, `RuntimeStore._reconcile_interrupted_runs_sync` (`storage/store.py:73`, see [persistence.md](persistence.md#startup-reconciliation)) catches anything the `finally` block missed — e.g., process SIGKILL, OOM, power loss mid-turn. It sweeps any tool run in `pending`/`running` and any assistant turn in `running` to `interrupted`, then logs the count. Load-bearing for crash recovery: without it, killed-mid-turn rows would appear "running" forever in the UI.

## Where streaming vs buffering is decided

Not here. `run_session` is always an async generator. Callers choose how to consume it:

- `/chat/stream` — iterate and forward each event as SSE.
- `/chat/message` — iterate, aggregate into a response object, return once `turn_finished` or `runtime_error` arrives.
- any future non-HTTP caller — iterate and handle events directly.

Streaming semantics (heartbeats, backpressure, disconnects) live in the transport layer, not in the runtime. See [transport.md](transport.md) for the producer/consumer queue that wraps this generator into SSE.
