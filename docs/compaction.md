# Compaction

Long conversations outgrow context windows. Rather than truncate the history or refuse to continue, the runtime **compacts** — summarizes old turns into a memo, drops the raw turns from the active prompt, and lets the conversation keep going. Compaction is transparent to the user (one info toast in the UI) and invisible to the tool layer.

This doc covers the trigger, the retention policy, how the summary is generated (LLM + heuristic fallback), and how compacted context re-enters the next model call.

## File map

- `backend/agent/compaction/policy.py` — trigger, retention policy, token estimation, two-phase algorithm, heuristic fallback.
- `backend/agent/compaction/summarizer.py` — LLM-backed summarizer with its own input-budget trimming and timeout.
- `backend/agent/compaction/token_counting.py` — `count_text_tokens` via `cl100k_base` tiktoken (lazy singleton, falls back to `len//4`).
- `backend/persistence/conversations/transcripts.py` + `backend/persistence/models.py` — persists summary turns + `compaction_summaries` rows; rebuilds wire messages with them via `message_builder.py`.

## The trigger

`compact_if_needed` (`compaction.py:241`) is called once per loop iteration from `ChatRuntime.run_session` (via `Turn.compact_if_needed`, see [runtime.md](runtime.md#the-iteration-loop)). Sequence:

1. Session must have a `context_window` set — otherwise compaction is off.
2. `estimate_active_tokens(store, session_id)` counts what the next call will cost.
3. If the total is at or under the window (and `force=False`), return `None` and proceed.
4. Otherwise hand off to `_compact_if_needed_inner` (`compaction.py:276`) — build a retention policy, prune old tool runs, summarize the oldest turns, write the summary turn.

The whole thing is wrapped in `asyncio.wait_for(..., timeout=COMPACTION_HARD_TIMEOUT_SECONDS)` (`compaction.py:66`, **45s**). If the summarizer wedges, the timeout fires, a warning is logged, and `compact_if_needed` returns `None` — the iteration proceeds without compaction. A subsequent `ContextOverflowError` will trigger a forced retry (below).

The returned dict is surfaced as a `compaction_started` `RuntimeEvent` with fields like `summary_turn_id`, `active_tokens_before`, `context_window`, `source_turn_count`, `summary_token_count`, and `summary_source` (`"llm"` / `"heuristic"` / `"prune_only"`). The UI shows a small notice; the transcript records it permanently.

## Forced compaction (context-overflow retry)

When the provider rejects a prompt our estimator was happy with (`ContextOverflowError`), the runtime catches it in `run_session`'s inner try, marks the current turn `error`, and calls `Turn.handle_overflow()` which sets `force_overflow_compaction=True`. The next iteration calls `compact_if_needed` with:

- `force=True` — run the full two-phase compaction unconditionally, even if `estimate_active_tokens` says we're under the window.
- `retention_budget_override = context_window // 4` — about a quarter of what we'd normally keep, so the active prompt shrinks dramatically.

One-shot per user turn. A second overflow falls through to `runtime_error`. See [runtime.md](runtime.md#context-overflow-recovery).

## Token estimation

`estimate_active_tokens` (`compaction.py:155`) walks the transcript and sums contributions from every non-compacted turn:

| What | Token source |
|------|--------------|
| Assistant turn | `output_tokens` reported by the provider. `max(1, output_tokens)` so zero-token turns still count. |
| Assistant turn with no usage data | `count_text_tokens(turn.text)` via tiktoken. |
| User turn | `count_text_tokens(turn.text)`. |
| Tool result (uncompacted, `tool_run.result` populated) | `count_text_tokens(tool_run.result)`. |
| Tool call | `count_text_tokens(tool_call_part.content) + TOOL_CALL_OVERHEAD_TOKENS` (20 tokens for the JSON envelope). |

The subtle bit is **assistant turns use `output_tokens`, not `input_tokens`** (`compaction.py:134-135`). `input_tokens` is what the provider billed — which includes every earlier message — so summing input_tokens across turns double-counts massively. An 11-turn session would read as ~150K "transcript tokens" when the real transcript is ~15K. Using output_tokens counts only what each turn *added*; tool calls and tool results are summed separately in the same function.

tiktoken is used as the fallback when the provider didn't report usage (mid-stream errors, test stub clients). Not byte-perfect across providers but accurate enough for a threshold decision. Not meant for billing — only for deciding *when* to compact.

## Retention policy

`RetentionPolicy.for_context_window` (`compaction.py:91`). Scales with the session's context window:

```
recent_raw_turns         = clamp(6, 24,  MIN + (window - 32K) // 16K)
recent_raw_tool_runs     = clamp(12, 40, MIN + (window - 32K) // 8K)
retention_budget_tokens  = max(200, window // 2)
```

`retention_budget_tokens` is the primary signal for turn selection. `recent_raw_turns` / `recent_raw_tool_runs` remain as bounds: `MAX_RECENT_RAW_TURNS=24` caps how many turns the budget can retain, and `_compact_old_tool_runs` uses `recent_raw_tool_runs` to trim completed tool results from inside the retained window.

### Token-weighted selection

`_select_source_turns` (`compaction.py:164`) walks newest→oldest and keeps each turn's full token weight (text + its uncompacted tool results + its tool-call parts — same formula as `estimate_active_tokens`). Stops when either the retention budget is exhausted or `MAX_RECENT_RAW_TURNS` is reached. `HARD_KEEP_FLOOR_TURNS=3` is a coherence minimum: we always keep the last 3 turns raw, even if they individually exceed the budget, so a single huge recent tool result can't orphan the user's last exchange.

This is the fix for the "one 5KB tool result counts the same as a one-line turn" problem. A uniform-dialog session with 150K window keeps up to 24 turns (budget easily absorbs them). A session with a 30K tool result in the last few turns retains only those few turns and compacts everything older — budget-aware, not count-aware.

If the walk selects nothing to compact (session is under the budget even before compacting anything), `compact_if_needed` returns `None` — the session is over the window but we can't drop anything without cutting into the hard floor.

Old tool runs (pre-tail, completed) are compacted **separately** in `_compact_old_tool_runs` (`compaction.py:199`) — not tied to turn compaction. This lets the summarizer still see intermediate tool results that landed on retained turns; only the very old ones are dropped from the active prompt. If pruning tool runs alone frees enough tokens to fit the window, Phase 2 (turn summarization) is skipped and the event reports `summary_source="prune_only"`.

## The summarizer

Two paths, `_build_summary` (`compaction.py:371`):

### LLM path

`summarize_for_compaction` (`summarizer.py:64`). Uses the provider's **summarizer_model** — a cheap sibling of the main model (e.g., Haiku for Anthropic, gpt-5-mini for OpenAI). Declared per provider in `backend/providers/__init__.py`. See [providers.md](providers.md#summarizer-model).

System prompt `SUMMARIZER_SYSTEM_PROMPT` (`summarizer.py:30`) instructs the model to produce a dense bulleted memo preserving:

- The user's overall goal.
- Concrete facts uncovered (names, seasons, stats, totals).
- Which tools ran and the gist of what they returned.
- Open questions / pending follow-ups.

Explicitly omitting small talk, retries, and tool input minutiae. Target 200–500 words.

### Input budget

Hard cap: `SUMMARIZER_INPUT_BUDGET_TOKENS = 60_000` (`summarizer.py:51`). Well below any real model's window — compaction should be fast and cheap, not another full-context call.

Input construction is two-pass (`summarizer.py:99-118`, via `_flatten_turns` at `summarizer.py:120`):

1. Flatten source turns with `tool_result_char_cap = 4000`.
2. If over budget, re-flatten with `tool_result_char_cap = 400`.

Tool inputs are capped at 400 chars regardless; tool results are the variable-length part worth shrinking. Each tool run is rendered as:

```
TOOL execute_sql (completed)
  input: {"sql":"SELECT ..."}
  result: {"columns":[...],"rows":[...],"row_count":500}
  error: (if any)
```

### Timeouts

There are **two** timeouts, layered:

- **Inner** (`SUMMARIZER_TIMEOUT_SECONDS = 30.0`, `summarizer.py:55`) — wraps just the provider `stream_message` call. Haiku / mini-class models typically respond in 2–5s; 30s absorbs cold starts. On timeout, the exception propagates to `_build_summary`, which catches it and falls back to the heuristic.
- **Outer** (`COMPACTION_HARD_TIMEOUT_SECONDS = 45.0`, `compaction.py:66`) — wraps the entire compaction including prune + summary. If this fires (summarizer wedged, slow DB, etc.), `compact_if_needed` returns `None` entirely and the iteration proceeds without compaction. A subsequent `ContextOverflowError` then triggers the forced-retry path.

### Heuristic fallback

`_heuristic_summary` (`compaction.py:222`). Plain string concatenation: `"- user: <preview>"`, `"- tool execute_sql (completed): input=<preview>"`. Preserves structure but not *findings* — the model can't tell that a previous query returned 500 rows about QBs; it only sees that `execute_sql` ran with a specific input.

The heuristic is strictly worse than the LLM summary for continued-investigation coherence. It exists only to make compaction **never block on network** — if the summarizer is unreachable, the conversation still makes progress.

Fallback triggers (all handled in `_build_summary`, `compaction.py:371`):

- No client supplied (offline tests, non-network contexts) — return heuristic directly.
- Any exception in `summarize_for_compaction` (API error, inner timeout, unknown provider, etc.) — log a warning and fall back.

## Persisting a summary

`RuntimeStore.record_compaction` (`backend/persistence/conversations/transcripts.py:191`) performs three writes in a single transaction:

1. Create a new turn with `role = "summary"` and the summary text.
2. Insert a `compaction_summaries` row recording `(summary_turn_id, source_turn_ids)`.
3. `UPDATE turns SET compacted = 1` on each source turn; `UPDATE tool_runs SET compacted = 1` on completed tool runs belonging to those turns.

The `compacted` flag is how every downstream consumer knows to skip a row. The source rows aren't deleted — the full transcript is still queryable (for the UI's inspector panel, for debugging), but the active-prompt builder filters them out.

`role = "summary"` is a third role alongside `user` and `assistant`. It only exists in the store — never sent to a provider in that shape.

## Re-injecting summaries into the next model call

`build_model_messages` (`backend/agent/message_builder.py:12`). This is where compacted context re-enters the wire protocol.

Order of emission:

1. **All non-compacted `role="summary"` turns first**, regardless of creation time, as a single synthetic `assistant` message.
2. Then each non-compacted `role="user"` / `role="assistant"` turn in chronological order, skipping summary turns.

Why summaries go first rather than chronologically: a summary represents compacted *prior* turns, so it belongs at the top of the active window as context. If emitted in chronological order, a summary turn created mid-session would leave an assistant-role message after the most recent user turn — which Anthropic rejects with `"conversation must end with a user message"`. Placing summaries first sidesteps that entire class of bug.

## The summary wrapping

`wrap_summaries_for_prompt` (`backend/persistence/models.py:33`). Multiple summaries (layered compactions over a very long session) are concatenated with `---` separators, then wrapped:

```
<prior_conversation_summary>
<memo 1>

---

<memo 2>
</prior_conversation_summary>

The block above is a compressed memo of earlier turns, provided
for context only. I will answer the user's next message naturally
in plain prose and will NOT reproduce the summary, its bullet-list
formatting, or any 'tool X (completed): input=…' lines in my reply.
```

Two things are happening here:

1. **Tag wrapping** — gives the model a clean delimiter so it can distinguish "memory" from "live conversation".
2. **Anti-mimic note** — the summary's format (dashes, "tool X (completed): input=" lines) is exactly the kind of pattern LLMs will copy into their replies if not told otherwise. The note heads that off. Users reported early versions where the assistant's reply started with bullet lists echoing the memo; the note + a matching rule in the system prompt fixed it.

The `Conversation Memory` section of the system prompt (`backend/agent/system_prompt.py`) reinforces this — both the wrapping note and the system prompt tell the model to treat the summary as private memory.

## Interaction with the iteration loop

Compaction is checked at the **top of every loop iteration** (`runtime.py:112-119`), not just at session start. This matters because a single turn can drive the loop through 10 iterations of tool_use → tool_result → model response, each one growing the active transcript. A session that was 80% of window at turn start can blow past the window by iteration 5.

Checking every iteration means the runtime can compact mid-turn. The session lock ensures no other turn is writing while this happens.

## What compaction doesn't do

- **Does not delete data.** Source turns stay in SQLite with `compacted=1`. The UI inspector can still show them.
- **Does not re-summarize.** Each compaction summarizes only the window *it* selected; existing summaries pass through untouched. Layered compactions just concatenate.
- **Does not run in the background.** It's synchronous within the iteration loop. A 5s summary call is a 5s pause before the model sees your next message. This is a deliberate simplicity trade-off — true background compaction would need coordination with the session lock and a retry path.
- **Does not touch tool *calls* on retained turns.** Only `completed` tool runs on *compacted* turns (and old completed tool runs via `_compact_old_tool_runs`) are marked compacted. In-flight runs and errored runs are preserved so the model can see what failed.
