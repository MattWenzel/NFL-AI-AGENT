# Runtime

The runtime is the heart of the agent: one class, `ChatRuntime`, drives every user turn through the model → tool loop → persistence pipeline. It is transport-agnostic — the same runtime powers `/chat/stream`, `/chat/message`, and `python -m cli.chat_cli`.

This doc covers the iteration loop, the event stream, the session lock, the doom-loop guard, and where errors surface. Compaction and tools have their own docs ([compaction.md](compaction.md), [tools.md](tools.md)).

## File map

- `agent/runtime/loop.py` — `ChatRuntime`: the loop itself.
- `agent/runtime/events.py` — `RuntimeEvent` and `RuntimeLoopError`.
- `agent/runtime/loop_detector.py` — `raise_if_doom_loop` heuristic.
- `agent/runtime/compaction.py`, `summarizer.py` — see [compaction.md](compaction.md).

## `ChatRuntime`

Defined at `agent/runtime/loop.py:48`. Two public entry points:

- `prepare_session(client, provider_name, conversation_id, user_id) -> SessionRecord` (`loop.py:54`) — resolve or create the session. Called from the transport layer before `run_session` so the API and CLI key sessions the same way (by provider, model, and context window).
- `run_session(session, user_text, client, *, tools, provider_name) -> AsyncIterator[RuntimeEvent]` (`loop.py:80`) — drive one user turn. Always consumes `client.stream_message`; non-streaming callers buffer events at the transport boundary.

The runtime holds a single dependency: a `RuntimeStore` ([persistence.md](persistence.md)). It does not reach into SQLite directly — every persistence concern is a store call.

## The iteration loop

A single call to `run_session` may drive the model through **up to `MAX_TOOL_ITERATIONS = 10` passes** (`loop.py:44`). Each pass is one `stream_message` call. The loop terminates when the model returns a text-only response (no tool calls) or when the budget is exhausted.

One full user turn looks like this:

```
run_session(user_text)
 ├─ acquire session lock                      # loop.py:97
 ├─ persist user turn                         # loop.py:101
 ├─ yield turn_started
 └─ for iteration in 1..MAX_TOOL_ITERATIONS:
      ├─ compact_if_needed  ─── yields compaction_started if triggered   # loop.py:112
      ├─ create assistant turn (status=running)                          # loop.py:123
      ├─ yield assistant_started
      ├─ async for event in client.stream_message(...):                  # loop.py:132
      │    ├─ TextEvent      → append to turn, add text part, yield text_delta
      │    └─ ToolUseEvent   → create tool_run, add tool_call part, yield tool_pending
      ├─ mark assistant turn completed (+usage)
      ├─ if no tool_runs:
      │    ├─ if stop_reason == MAX_TOKENS: raise RuntimeLoopError
      │    └─ yield turn_finished, return
      ├─ raise_if_doom_loop(tool_runs)                                   # loop.py:187
      ├─ await asyncio.gather(_execute_tool(r) for r in tool_runs)       # loop.py:189
      │    └─ each result → yield tool_completed or tool_failed
      └─ yield assistant_requires_followup   ─── loop continues
 └─ (budget exhausted) yield runtime_error
```

Two things about this shape are worth calling out:

1. **Tools run concurrently within a pass.** `asyncio.gather` at `loop.py:189` fires all tool calls from one `stream_message` pass in parallel. Handlers are wrapped in `asyncio.to_thread` so blocking SQLite I/O doesn't stall the event loop (see [tools.md](tools.md)).
2. **No tool calls = turn done.** The only exit from the loop is the model emitting a pure-text response (`loop.py:176`). This is why guides, schema fetches, and SQL calls all ultimately return to the model for synthesis — the model must produce at least one text turn to finish.

## Session lock

Every turn runs under `self.store.lock(session.id)` (`loop.py:97`). The lock is a per-session asyncio mutex held by the store; concurrent requests against the same session serialize. This matters for two reasons:

- **Transcript consistency.** Turns, assistant parts, and tool runs are written in strict order, so `build_model_messages` always sees a coherent history.
- **No double-spending.** If a user fires two messages in the same conversation at once, the second blocks until the first's loop exits.

Cross-session calls are unaffected — the lock is keyed by `session.id`, not global.

## `RuntimeEvent`

One dataclass, one discriminator field, defined at `agent/runtime/events.py:13`:

| Field | Type | Used by |
|-------|------|---------|
| `type` | `str` | discriminator — see catalog below |
| `session_id` | `str` | always set |
| `turn_id` | `str \| None` | set for turn-scoped events |
| `text` | `str \| None` | `text_delta` |
| `tool_run_id` | `str \| None` | tool lifecycle events |
| `name` | `str \| None` | tool name |
| `input` | `dict \| None` | tool call input |
| `result` | `str \| None` | tool result (string — may be JSON) |
| `error` | `str \| None` | error message |
| `iterations` | `int \| None` | current iteration in the loop |
| `status` | `str \| None` | reserved; unused by current emitters |
| `meta` | `dict \| None` | compaction metadata |

### Event catalog

| `type` | Emitted at | Payload |
|--------|-----------|---------|
| `turn_started` | after user turn persisted (`loop.py:107`) | `turn_id` of the user turn |
| `compaction_started` | when `compact_if_needed` returns metadata (`loop.py:116`) | `meta` = compaction info dict; `turn_id` = summary turn |
| `assistant_started` | new assistant turn opened (`loop.py:125`) | `turn_id`, `iterations` |
| `text_delta` | per `TextEvent` from stream (`loop.py:140`) | `text`, `turn_id`, `iterations` |
| `tool_pending` | per `ToolUseEvent` (`loop.py:160`) | `tool_run_id`, `name`, `input` |
| `tool_completed` | tool returned successfully (`loop.py:191`) | `tool_run_id`, `name`, `result` |
| `tool_failed` | tool errored (`loop.py:191` — same emitter, `status != "completed"`) | `tool_run_id`, `name`, `error`, `result` (partial) |
| `assistant_requires_followup` | after tools complete, before next loop pass (`loop.py:202`) | `turn_id`, `iterations` |
| `turn_finished` | text-only response — loop exits normally (`loop.py:183`) | `turn_id`, `iterations` |
| `runtime_error` | `RuntimeLoopError` or iteration budget exhausted (`loop.py:215`, `loop.py:232`) | `error`, `iterations` |

Transports serialize these differently. The SSE adapter drops some internal events (`assistant_started`, `turn_started`) and renames others for the browser — see [transport.md](transport.md).

## `RuntimeLoopError`

Defined at `events.py:29`. One exception type for unrecoverable loop states. Three things raise it:

1. **MAX_TOKENS with no tool call** (`loop.py:177`). The model hit its output cap mid-text with nothing to follow up on. Surfaced as a user-facing `runtime_error` with guidance to ask a narrower question or reply "continue".
2. **Doom loop** (`loop_detector.py:29`). See below.
3. **(Conceptually)** — iteration budget exhaustion, though this is emitted directly as `runtime_error` rather than raised (`loop.py:232`).

`RuntimeLoopError` is caught specifically at `loop.py:205`; the assistant turn is marked `status="error"`, a `runtime_error` event is yielded, and the loop returns cleanly. Any other exception (`loop.py:223`) also marks the turn errored but is re-raised — the runtime doesn't swallow unknown errors.

## Doom-loop detector

`loop_detector.py:20`. Before dispatching a pass's tool calls, the runtime fingerprints the last `DOOM_LOOP_WINDOW = 6` tool runs in the store plus the ones queued for the current pass. If the **tail `DOOM_LOOP_MATCH = 3`** fingerprints are identical (same `tool_name`, same `input_json`), it raises.

Why this shape: a model stuck in a loop typically repeats the *same* call over and over. Three identical calls in a row is a strong signal — it's unlikely in healthy use (the model usually varies SQL between retries) and catches common failure modes before the iteration budget exhausts.

The fingerprint uses raw `input_json` string equality. This is intentional: semantically-equivalent JSON with different key ordering isn't caught, but both sides of the comparison come from the same serializer (`create_tool_run` writes canonical JSON), so equality is stable in practice.

## Tool execution (`_execute_tool`)

`loop.py:256`. Called once per queued tool run under `asyncio.gather`. Responsibilities:

1. Mark the tool run `running`; emit a `tool_status` part so the UI can show the spinner.
2. Decode `input_json`. Malformed JSON is logged, persisted as an error, and returned as an error envelope — the model sees the error message as the tool result.
3. Build a `ctx` dict (`loop.py:282`) with a `register_export` callback. This is the **side-channel** handlers use to reach the persistence layer without importing it. `create_csv_export` uses it to index generated files in the export library.
4. Call `execute_tool_structured(name, input, ctx=ctx)` (see [tools.md](tools.md)).
5. Persist the result: status, result text, error text, hint, duration, plus a `tool_result` assistant part.

The handler never directly touches the store. Everything flows through `ctx` or the return envelope. This is what keeps `agent/tools/` free of infra dependencies.

## Cleanup on early exit

The `finally` block at `loop.py:238` marks any still-running assistant turn or tool run as `interrupted`. Two scenarios trigger this:

- The client disconnects mid-stream (FastAPI cancels the generator).
- An unexpected exception propagates past the loop body.

On next startup, `RuntimeStore.reconcile_interrupted_runs` (see [persistence.md](persistence.md)) catches anything the finally block missed — e.g., if the process is killed.

## Where streaming vs buffering is decided

Not here. `ChatRuntime.run_session` is always an async generator. Callers choose:

- `/chat/stream` — iterate and forward each event as SSE.
- `/chat/message` — iterate, collect into a response object, return once `turn_finished` or `runtime_error` arrives.
- CLI — iterate and print to stdout.

This means streaming semantics (heartbeats, backpressure, disconnects) live in the transport layer, not in the runtime. See [transport.md](transport.md) for the producer/consumer queue that wraps this generator into SSE.
