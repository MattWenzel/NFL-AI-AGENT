"""Context-window compaction for long-running chat sessions.

Strategy: once the estimated active token count exceeds the session's
configured context window, summarize the oldest turns into a single
"earlier conversation summary" message and mark the source turns as
compacted. Old tool results are also marked compacted so they drop out
of the active prompt.

Token counting is character-based (~4 chars per token) for text and uses
the provider-reported input+output tokens for assistant turns when
available. Precise enough to decide *when* to compact; not meant for
billing.
"""

import json

from infra.persistence.runtime_store import (
    RuntimeStore,
    SessionRecord,
    TurnRecord,
    safe_load_tool_input,
)


CHARS_PER_TOKEN = 4
RECENT_RAW_TURNS = 6
RECENT_RAW_TOOL_RUNS = 12
# Rough allowance for the tool-call JSON envelope (tokens) on top of the
# character-based estimate for the inner input payload.
TOOL_CALL_OVERHEAD_TOKENS = 20
COMPACTION_TEXT_PREVIEW_CHARS = 240
COMPACTION_TOOL_INPUT_PREVIEW_CHARS = 160


def _estimate_turn_tokens(turn: TurnRecord) -> int:
    if turn.role == "assistant" and (turn.input_tokens or turn.output_tokens):
        return max(1, turn.input_tokens + turn.output_tokens)
    return max(1, len(turn.text or "") // CHARS_PER_TOKEN)


def estimate_active_tokens(store: RuntimeStore, session_id: str) -> int:
    transcript = store.get_transcript(session_id)
    total = 0
    for turn in transcript.turns:
        if turn.compacted:
            continue
        total += _estimate_turn_tokens(turn)
        for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
            if not tool_run.compacted and tool_run.result_text:
                total += max(1, len(tool_run.result_text) // CHARS_PER_TOKEN)
        for part in transcript.parts_by_turn.get(turn.id, []):
            if part.kind == "tool_call":
                total += max(1, len(part.content) // CHARS_PER_TOKEN) + TOOL_CALL_OVERHEAD_TOKENS
    return total


def _compact_old_tool_runs(store: RuntimeStore, session_id: str) -> None:
    transcript = store.get_transcript(session_id)
    active_completed = [
        run
        for runs in transcript.tool_runs_by_turn.values()
        for run in runs
        if not run.compacted and run.status == "completed"
    ]
    if len(active_completed) <= RECENT_RAW_TOOL_RUNS:
        return
    stale = active_completed[:-RECENT_RAW_TOOL_RUNS]
    for run in stale:
        store.update_tool_run(run.id, compacted=1)


def compact_if_needed(store: RuntimeStore, session: SessionRecord) -> dict | None:
    """Compact oldest turns when active tokens exceed the session's context window.

    Returns a dict describing the compaction (summary turn id, source turn
    ids, token counts) when one occurs, else None.
    """
    if not session.context_window:
        return None
    active_tokens = estimate_active_tokens(store, session.id)
    if active_tokens <= session.context_window:
        return None
    transcript = store.get_transcript(session.id)
    active_turns = [t for t in transcript.turns if not t.compacted and t.role in {"user", "assistant"}]
    if len(active_turns) <= RECENT_RAW_TURNS:
        return None
    source_turns = active_turns[:-RECENT_RAW_TURNS]
    if not source_turns:
        return None
    summary_lines = ["Earlier conversation summary:"]
    for turn in source_turns:
        text = (turn.text or "").strip()
        if text:
            preview = text.replace("\n", " ")[:COMPACTION_TEXT_PREVIEW_CHARS]
            summary_lines.append(f"- {turn.role}: {preview}")
        for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
            input_data = safe_load_tool_input(tool_run.input_json, tool_run_id=tool_run.id)
            summary_lines.append(
                f"- tool {tool_run.tool_name} ({tool_run.status}): "
                f"input={json.dumps(input_data, sort_keys=True)[:COMPACTION_TOOL_INPUT_PREVIEW_CHARS]}"
            )
    summary = store.record_compaction(
        session.id,
        "\n".join(summary_lines),
        [turn.id for turn in source_turns],
    )
    _compact_old_tool_runs(store, session.id)
    return {
        "summary_turn_id": summary.summary_turn_id,
        "source_turn_ids": summary.source_turn_ids,
        "active_tokens_before": active_tokens,
        "context_window": session.context_window,
    }
