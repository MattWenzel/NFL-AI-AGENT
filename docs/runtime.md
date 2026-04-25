# Runtime

The runtime is the heart of the agent: one class, `ChatRuntime`, drives every user turn through the model → tool loop → persistence pipeline. It is transport-agnostic — the same runtime powers `/chat/stream`, `/chat/message`, and any future non-HTTP entry point.

This doc covers the iteration loop, the event stream, the session lock, doom-loop protection, context-overflow recovery, and cleanup on early exit. Compaction and tools have their own docs ([compaction.md](compaction.md), [tools.md](tools.md)).

## File map

The `backend/agent/` package is split by concern. The runtime orchestrator and the per-user-message state machine live in two files; the rest are helpers.

- `backend/agent/runtime.py` — `ChatRuntime`: the loop itself. Thin orchestrator that acquires the session lock, instantiates a `Turn`, and iterates.
- `backend/agent/turn.py` — `Turn`: per-user-message state owner. Holds iteration bookkeeping (counter, overflow retry one-shot, doom-loop fingerprint list, forced tool-choice), the active assistant iteration (its `TurnRecord`, text buffer, tool runs), lifecycle methods, and in-turn tool execution. Module-level `raise_if_doom_loop` + `DOOM_LOOP_MATCH` also live here.
- `backend/agent/events.py` — `RuntimeEvent` dataclass + `RuntimeLoopError` exception.
- `backend/agent/message_builder.py` — `build_model_messages`: `SessionTranscript` → wire-format `list[Message]` for the next provider call.
- `backend/agent/system_prompt.py` — `get_base_prompt`: the slim base system prompt (rules + guide index).
- `backend/agent/compaction/policy.py`, `backend/agent/compaction/summarizer.py`, `backend/agent/compaction/token_counting.py` — see [compaction.md](compaction.md).

## `ChatRuntime`

Defined at `backend/agent/runtime.py:42`. Minimal constructor:

```python
class ChatRuntime:
    def __init__(self, store: RuntimeStore):
        self.store = store
```

`store` is the only external dependency. A fresh `Turn` is built inside `run_session` per user message — the runtime is stateless across calls.

Public entry points:

- `prepare_session(client, provider_name, conversation_id, user_id) -> SessionRecord` — resolve or create a session. Called from the transport layer (via `ChatService.prepare_chat`) so every caller keys sessions the same way (by provider, model, context window).
- `run_session(session, user_text, client, *, tools, provider_name, tool_choice=None) -> AsyncIterator[RuntimeEvent]` — drive one user turn. Always consumes `client.stream_message`; non-streaming callers buffer the events at the transport boundary.

## The iteration loop

`MAX_TOOL_ITERATIONS = 10`. A single `run_session` drives the model through up to 10 passes. Each pass is one `client.stream_message` call. The loop exits when the model returns a text-only response (no tool calls), when the budget is exhausted, or on an unrecoverable error.

```
run_session(user_text)
 ├─ acquire session lock
 ├─ store.create_turn(..., role="user")
 ├─ store.update_session(...) (title on 1st turn)
 ├─ yield turn_started
 ├─ Turn(store, persistence, session, execute_tool, initial_tool_choice)
 └─ for _ in range(MAX_TOOL_ITERATIONS):
      ├─ turn.begin_iteration (bumps iterations, consumes forced tool_choice)
      ├─ turn.compact_if_needed → maybe yield compaction_started
      ├─ turn.open_assistant_turn → yield assistant_started
      ├─ reset client.last_usage / last_stop_reason
      ├─ transcript = await store.get_transcript(...)
      ├─ async for event in client.stream_message(...):
      │    ├─ RetryingEvent     → yield retrying
      │    ├─ TextEvent         → turn.record_text_delta   → yield text_delta
      │    └─ ToolUseEvent      → turn.record_tool_call    → yield tool_pending
      ├─ turn.complete_assistant_turn
      │    ├─ if no tool_runs   → yield turn_finished, return
      │    └─ else              → fall through to tool dispatch
      ├─ turn.record_tool_runs (also calls raise_if_doom_loop)
      ├─ turn.execute_tools (asyncio.gather)
      ├─ yield tool_completed / tool_failed per result
      ├─ yield assistant_requires_followup
      └─ turn.reset_active_iteration
   (budget exhausted) → yield turn.max_iterations_event
finally:
    if turn.has_active_assistant_turn: turn.cleanup_interrupted_assistant_turn
```

Three things worth calling out about this shape:

1. **Tools run concurrently within a pass.** `turn.execute_tools` is `asyncio.gather` over the pass's tool runs. Each handler runs in `asyncio.to_thread` because the SQL sandbox is sync (see [tools.md](tools.md)).
2. **No tool calls = turn done.** The only normal exit from the loop is the model emitting a pure-text response — `turn.complete_assistant_turn` returns a `turn_finished` event in that case and the runtime returns. This is why guides, schema fetches, and SQL calls all ultimately cycle back to the model for synthesis.
3. **Persistence stays behind `RuntimeStore`.** `ChatRuntime` performs session-level writes directly through the store; `Turn` owns the high-frequency assistant/tool writes for a single user message.

## Session lock

Every turn runs under `self.store.lock(session.id)`. The lock is a per-session `asyncio.Lock` held by the store; concurrent requests to the same conversation serialize. Two reasons it matters:

- **Transcript consistency.** Turns, assistant parts, and tool runs are written in strict order, so `build_model_messages` on the next pass always sees a coherent history.
- **No double-spending.** If a user fires two messages in the same conversation at once, the second blocks until the first's loop exits.

Cross-session calls are unaffected — the lock is keyed by `session.id`, not global. The lock lives on `RuntimeStore` (not on `Turn`) because it must survive across `Turn` instances for the same session.

## `Turn` — per-user-message state

`backend/agent/turn.py`. One object, constructed at the start of each `run_session`, lives for the duration of that user message. Consolidates what used to be spread across `RuntimeLoopState`, `AssistantTurnManager`, `AssistantTurnContext`, `AssistantTextBuffer`, and `ToolExecutionService`.

**Per-user-turn fields** (persist across the 1..N assistant iterations):

- `iterations: int` — loop counter.
- `force_tool_choice_next_iter: ToolChoice | None` — override consumed by `begin_iteration`.
- `force_overflow_compaction: bool` — set by `handle_overflow()` so the next iteration forces compaction.
- `overflow_retry_used: bool` — one-shot flag; a second overflow falls through.
- `user_turn_tool_runs: list[ToolRunRecord]` — accumulates every tool run this user turn for doom-loop fingerprinting.

**Active-iteration fields** (reset between iterations):

- `_active_assistant_turn: TurnRecord | None` — the currently-open assistant turn.
- `_active_tool_runs: list[ToolRunRecord]` — tool calls queued during the current iteration.
- `_active_text_buffer: _TextBuffer | None` — the 2048-char coalescing buffer for streamed text.

Exposed read-only via `has_active_assistant_turn`, `active_assistant_turn_id`, `active_tool_runs` properties. `reset_active_iteration()` is called by the runtime after each normal iteration boundary so the `finally` cleanup only fires on a genuine mid-iteration abort.

**Methods called by the runtime:**

| Method | What it does |
|---|---|
| `begin_iteration()` | Bumps `iterations`, returns the consumed forced tool choice. |
| `compact_if_needed(client, provider_name)` | Calls `agent.compaction.compact_if_needed`; forces aggressive compaction when `force_overflow_compaction` is armed. |
| `compaction_event(meta)` | Builds the `compaction_started` `RuntimeEvent`. |
| `open_assistant_turn()` | Persists a new assistant `TurnRecord` (status=running), seeds the text buffer + tool-runs list. Returns `assistant_started`. |
| `record_text_delta(text)` | Appends to the buffer (flushes at 2048 chars), returns `text_delta`. |
| `record_tool_call(event)` | Flushes the buffer, persists the tool-run + tool-call part, appends to `active_tool_runs`. Returns `tool_pending`. |
| `complete_assistant_turn(usage, stop_reason)` | Flushes, marks the turn `completed`, records usage. Returns `turn_finished` only when no tool calls were queued; otherwise None. Raises `RuntimeLoopError` on MAX_TOKENS with no tool call. |
| `mark_assistant_turn_error(error, usage)` | Flushes, marks the turn `error`. No-op if no iteration is active. |
| `cleanup_interrupted_assistant_turn()` | From the runtime's finally block. Marks any still-`running` turn + `pending`/`running` tool runs `interrupted`. |
| `reset_active_iteration()` | Clears active-iteration refs between iterations. |
| `record_tool_runs(tool_runs)` | Extends `user_turn_tool_runs`, calls `raise_if_doom_loop`. |
| `execute_tools()` | `asyncio.gather` over queued tool calls. Returns results in `active_tool_runs` order. |
| `handle_overflow()` | First call arms forced compaction + returns True; second returns False. |
| `max_iterations_event(max)` | Builds the terminal `runtime_error` event when the budget exhausts. |

`TITLE_PREVIEW_CHARS = 80` (also `backend/agent/turn.py`) is the truncation length for the auto-generated session title.

## `RuntimeEvent`

One dataclass, one discriminator field (`backend/agent/events.py`). Relevant fields per event:

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
| `turn_started` | after user turn persisted | `turn_id` of the user turn |
| `compaction_started` | when `turn.compact_if_needed` returns metadata | `meta`, `turn_id` = summary turn |
| `assistant_started` | new assistant turn opened | `turn_id`, `iterations` |
| `retrying` | provider transient error mid-stream | `attempt`, `delay_seconds`, `error` |
| `text_delta` | per `TextEvent` from the stream | `text`, `turn_id`, `iterations` |
| `tool_pending` | per `ToolUseEvent` | `tool_run_id`, `name`, `input` |
| `turn_finished` | text-only response, no tool calls (via `turn.complete_assistant_turn`) | `turn_id`, `iterations` |
| `tool_completed` / `tool_failed` | after `turn.execute_tools` | `tool_run_id`, `name`, `result`, `error` |
| `assistant_requires_followup` | after tools, before next pass | `turn_id`, `iterations` |
| `runtime_error` | overflow retry exhausted, `RuntimeLoopError`, or max iterations | `error`, `iterations` |

Transports serialize these differently. The SSE adapter (`backend/server/sse.py`) drops internal events like `turn_started` / `turn_finished` / `assistant_requires_followup` and renames others for the browser — see [transport.md](transport.md#sse-event-catalog).

## Tool execution

`Turn.execute_tools` (`backend/agent/turn.py`). Called once per pass from the runtime:

1. `execute_tools` fans out to `_execute_one_tool` per tool run under `asyncio.gather`.
2. Each `_execute_one_tool` calls `persistence.begin_tool_execution` — flips status to `running`, writes a `tool_status` part so the UI can show a spinner.
3. It then calls the injected `execute_tool` callable (wired to `execute_tool_structured` — see [tools.md](tools.md)) with the tool name, input dict, and a `ctx` dict carrying a `register_export` callback. That callback is the **side-channel** handlers use to reach persistence without importing it (`create_csv_export` uses it to index generated files in the export library).
4. Wraps the return value in a `ToolExecutionResult(status, content, error, hint, duration_ms)`.
5. Calls `persistence.complete_tool_execution` — updates status/result/error/hint/duration and writes a `tool_result` part.

Handlers never touch the store. Everything flows through `ctx` or the return envelope — this is what keeps `backend/tools/` free of infra dependencies.

## Doom-loop detector

`raise_if_doom_loop` in `backend/agent/turn.py`. Called from `Turn.record_tool_runs` **before** tool dispatch for the current pass. If the tail `DOOM_LOOP_MATCH = 3` tool runs in `user_turn_tool_runs` have the same `tool_name` and the same canonical-JSON `input`, raises `RuntimeLoopError("Detected repeated tool loop on <tool> with identical input")`.

Why this shape: a model stuck in a loop typically repeats the *same* call over and over. Three identical calls in a row is a strong signal — healthy use varies SQL between retries, so three in a row catches failure modes before the iteration budget exhausts.

The input fingerprint is the `ToolInputJSON` column's on-disk representation — `json.dumps(input, sort_keys=True)` — so two semantically-equivalent dicts always fingerprint identically (see [persistence.md](persistence.md#file-map)).

Scope is one user turn. `user_turn_tool_runs` is cleared when the next user message arrives (a new `Turn` is constructed), so a follow-up like "try again" doesn't trip the detector on the first repeat.

## Context-overflow recovery

Providers sometimes reject a prompt our estimator was happy with — typically due to cumulative tool-result size we under-counted. `ContextOverflowError` is raised by the provider adapter (`backend/providers/base.py`) and caught in `run_session`:

1. Mark the current assistant turn `error` via `turn.mark_assistant_turn_error`.
2. `turn.reset_active_iteration()` so the finally-block cleanup doesn't double-fire.
3. `turn.handle_overflow()`:
   - If this is the first overflow this user turn: sets `force_overflow_compaction=True` and `overflow_retry_used=True`, returns `True`.
   - If it's the second: returns `False`.
4. If `True`: `continue` the loop. Next iteration's `turn.compact_if_needed` runs with `force=True` and an aggressive `retention_budget_override = context_window // 4`, so the prompt shrinks dramatically before we re-enter the provider.
5. If `False`: yield a `runtime_error` event and return — the user sees the overflow message and can retry.

One-shot retry per user turn. No exponential backoff, no multiple compactions — if a single aggressive compaction doesn't fit, further compaction is unlikely to help.

## `RuntimeLoopError`

`events.py`. One exception for unrecoverable loop states. Raised by:

1. **Doom loop** — `raise_if_doom_loop` (above).
2. **MAX_TOKENS with no tool call** — raised inside `turn.complete_assistant_turn` when the model hit its output cap mid-text with nothing to follow up on. Surfaced as a user-facing `runtime_error` with guidance to ask a narrower question or say "continue".

Caught by the runtime: the assistant turn is marked `error`, a `runtime_error` event is yielded, the loop returns cleanly. Any other exception also marks the turn `error` but is re-raised — the runtime doesn't swallow unknown errors.

## Cleanup on early exit

The `finally` block at the end of `run_session` calls `turn.cleanup_interrupted_assistant_turn()` when an iteration is still active on the way out. Two triggers:

- The client disconnects mid-stream (FastAPI cancels the generator; see [transport.md](transport.md#producerconsumer--heartbeat)).
- An unexpected exception propagates past the loop body.

`cleanup_interrupted_assistant_turn` flushes the text buffer and flips the assistant turn + any pending tool runs to `interrupted`. It relies on the forward-only status invariant: a turn already in `completed` or `error` is never regressed to `interrupted`.

On next startup, `RuntimeStore._reconcile_interrupted_runs_sync` (see [persistence.md](persistence.md#startup-reconciliation)) catches anything the `finally` block missed — e.g., process SIGKILL, OOM, power loss mid-turn. It sweeps any tool run in `pending`/`running` and any assistant turn in `running` to `interrupted`, then logs the count. Load-bearing for crash recovery: without it, killed-mid-turn rows would appear "running" forever in the UI.

## Where streaming vs buffering is decided

Not here. `run_session` is always an async generator. Callers choose how to consume it:

- `/chat/stream` — iterate and forward each event as SSE.
- `/chat/message` — iterate, aggregate into a response object, return once `turn_finished` or `runtime_error` arrives.
- any future non-HTTP caller — iterate and handle events directly.

Streaming semantics (heartbeats, backpressure, disconnects) live in the transport layer, not in the runtime. See [transport.md](transport.md) for the producer/consumer queue that wraps this generator into SSE.
