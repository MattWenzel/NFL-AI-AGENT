"""Context-window compaction for long-running chat sessions.

Strategy: once the estimated active token count exceeds the session's
configured context window, summarize the oldest turns into a single
"earlier conversation summary" message and mark the source turns as
compacted. Old tool results are also marked compacted so they drop out
of the active prompt.

Token counting uses provider-reported input+output tokens for assistant
turns when available, and a tiktoken-based estimate (see
`infra.token_counting`) for everything else. Precise enough to decide
*when* to compact; not meant for billing.
"""

import json
import logging
from dataclasses import dataclass

from infra.persistence.runtime_store import (
    AssistantPartRecord,
    RuntimeStore,
    SessionRecord,
    ToolRunRecord,
    TurnRecord,
    safe_load_tool_input,
)
from infra.providers import BaseLLMClient, get_provider
from infra.token_counting import count_text_tokens

from agent.runtime.summarizer import summarize_for_compaction

logger = logging.getLogger(__name__)


# Floor values — used when a session has no context_window configured,
# and as the minimum for the scaled policy. Sized so small-window
# providers (e.g. 32K budgets) aren't forced to keep more than they can.
MIN_RECENT_RAW_TURNS = 6
MIN_RECENT_RAW_TOOL_RUNS = 12
MAX_RECENT_RAW_TURNS = 24
MAX_RECENT_RAW_TOOL_RUNS = 40

# Retention is primarily token-weighted: walk backward from the most
# recent turn and keep turns until we've exhausted this budget. A turn's
# weight is its text tokens + its tool-result tokens + its tool-call
# tokens (same formula as estimate_active_tokens), so one 20K-token SQL
# dump costs 20K of budget, not "one turn slot". HARD_KEEP_FLOOR_TURNS
# is a coherence minimum we never cut below — it takes priority over the
# budget so a single huge recent turn can't orphan the user's last
# question mid-exchange.
HARD_KEEP_FLOOR_TURNS = 3
MIN_RETENTION_BUDGET_TOKENS = 200

# Rough allowance for the tool-call JSON envelope (tokens) on top of the
# token estimate for the inner input payload.
TOOL_CALL_OVERHEAD_TOKENS = 20
COMPACTION_TEXT_PREVIEW_CHARS = 240
COMPACTION_TOOL_INPUT_PREVIEW_CHARS = 160


@dataclass(frozen=True)
class RetentionPolicy:
    """How much recent raw context to keep, scaled to the session's window.

    `recent_raw_turns` / `recent_raw_tool_runs` are count-based bounds:
    the selector never keeps more turns than `recent_raw_turns` capped
    at MAX_RECENT_RAW_TURNS, and `_compact_old_tool_runs` uses
    `recent_raw_tool_runs` as its own trim threshold for completed tool
    runs inside the retained window.

    `retention_budget_tokens` is the primary signal for
    `_select_source_turns`: the walk keeps recent turns until the
    cumulative *token weight* of kept turns (text + tool results + tool
    calls) exceeds this budget. That way a single 20K-token tool result
    can't fill the "last 6 turns" slot and defeat compaction.
    """

    recent_raw_turns: int
    recent_raw_tool_runs: int
    retention_budget_tokens: int

    @classmethod
    def for_context_window(cls, context_window: int | None) -> "RetentionPolicy":
        if not context_window:
            return cls(
                recent_raw_turns=MIN_RECENT_RAW_TURNS,
                recent_raw_tool_runs=MIN_RECENT_RAW_TOOL_RUNS,
                retention_budget_tokens=MIN_RETENTION_BUDGET_TOKENS,
            )
        # Anchor scaling at 32K: every +16K of window buys one extra
        # raw turn and every +8K buys one extra raw tool run. Below 32K
        # the offset is negative and the clamp floors us at the minimum.
        raw_turns_scaled = MIN_RECENT_RAW_TURNS + (context_window - 32_000) // 16_000
        raw_tool_runs_scaled = MIN_RECENT_RAW_TOOL_RUNS + (context_window - 32_000) // 8_000
        # Half the window reserved for recent raw context; the other half
        # absorbs the summary, system prompt, tool-call envelopes, and
        # response headroom. Floor at MIN_RETENTION_BUDGET_TOKENS so tiny
        # test windows still make forward progress.
        retention_budget = max(MIN_RETENTION_BUDGET_TOKENS, context_window // 2)
        return cls(
            recent_raw_turns=max(
                MIN_RECENT_RAW_TURNS, min(MAX_RECENT_RAW_TURNS, raw_turns_scaled)
            ),
            recent_raw_tool_runs=max(
                MIN_RECENT_RAW_TOOL_RUNS,
                min(MAX_RECENT_RAW_TOOL_RUNS, raw_tool_runs_scaled),
            ),
            retention_budget_tokens=retention_budget,
        )


def _estimate_turn_tokens(turn: TurnRecord) -> int:
    """Tokens the turn's own text contributes, excluding its tool runs/calls.

    For assistant turns, `input_tokens` is the full prompt size Anthropic
    charged for that call — it already includes every earlier message.
    Summing input_tokens across turns double-counts history massively
    (an 11-turn session can read as 150K "transcript tokens" when the
    real transcript is ~15K). We want only what this turn adds, which
    is its output text, captured in `output_tokens`.

    We still fall back to tiktoken-on-text when the provider didn't
    report usage (mid-stream errors, CLI stub clients), so the estimator
    stays meaningful in those cases.
    """
    if turn.role == "assistant" and turn.output_tokens:
        return max(1, turn.output_tokens)
    return count_text_tokens(turn.text or "")


def _turn_keep_cost(
    turn: TurnRecord,
    tool_runs_by_turn: dict[str, list[ToolRunRecord]],
    parts_by_turn: dict[str, list[AssistantPartRecord]],
) -> int:
    """Total tokens this turn contributes when kept raw in the active prompt."""
    cost = _estimate_turn_tokens(turn)
    for tool_run in tool_runs_by_turn.get(turn.id, []):
        if not tool_run.compacted and tool_run.result_text:
            cost += count_text_tokens(tool_run.result_text)
    for part in parts_by_turn.get(turn.id, []):
        if part.kind == "tool_call":
            cost += count_text_tokens(part.content) + TOOL_CALL_OVERHEAD_TOKENS
    return cost


def estimate_active_tokens(store: RuntimeStore, session_id: str) -> int:
    transcript = store.get_transcript(session_id)
    return sum(
        _turn_keep_cost(turn, transcript.tool_runs_by_turn, transcript.parts_by_turn)
        for turn in transcript.turns
        if not turn.compacted
    )


def _select_source_turns(
    active_turns: list[TurnRecord],
    tool_runs_by_turn: dict[str, list[ToolRunRecord]],
    parts_by_turn: dict[str, list[AssistantPartRecord]],
    policy: RetentionPolicy,
) -> list[TurnRecord]:
    """Pick the prefix of active_turns to compact.

    Walks newest→oldest, accumulating the token weight of each turn.
    Stops when either the retention budget is exhausted or we've already
    kept MAX_RECENT_RAW_TURNS turns. HARD_KEEP_FLOOR_TURNS recent turns
    are always kept (even past the budget) so a single huge recent turn
    can't leave the user's last exchange orphaned.
    """
    kept_count = 0
    kept_tokens = 0
    # Exclusive index into active_turns: everything before `boundary` is compacted.
    boundary = len(active_turns)
    for i in range(len(active_turns) - 1, -1, -1):
        cost = _turn_keep_cost(active_turns[i], tool_runs_by_turn, parts_by_turn)
        if kept_count < HARD_KEEP_FLOOR_TURNS:
            kept_tokens += cost
            kept_count += 1
            boundary = i
            continue
        if kept_count >= MAX_RECENT_RAW_TURNS:
            break
        if kept_tokens + cost > policy.retention_budget_tokens:
            break
        kept_tokens += cost
        kept_count += 1
        boundary = i
    return active_turns[:boundary]


def _compact_old_tool_runs(
    store: RuntimeStore, session_id: str, policy: RetentionPolicy
) -> None:
    transcript = store.get_transcript(session_id)
    active_completed = [
        run
        for runs in transcript.tool_runs_by_turn.values()
        for run in runs
        if not run.compacted and run.status == "completed"
    ]
    if len(active_completed) <= policy.recent_raw_tool_runs:
        return
    stale = active_completed[: -policy.recent_raw_tool_runs]
    for run in stale:
        store.update_tool_run(run.id, compacted=1)


def _heuristic_summary(
    source_turns: list[TurnRecord],
    tool_runs_by_turn: dict[str, list[ToolRunRecord]],
) -> str:
    """Fallback summary used when the LLM summarizer is unavailable."""
    lines = ["Earlier conversation summary:"]
    for turn in source_turns:
        text = (turn.text or "").strip()
        if text:
            preview = text.replace("\n", " ")[:COMPACTION_TEXT_PREVIEW_CHARS]
            lines.append(f"- {turn.role}: {preview}")
        for tool_run in tool_runs_by_turn.get(turn.id, []):
            input_data = safe_load_tool_input(tool_run.input_json, tool_run_id=tool_run.id)
            lines.append(
                f"- tool {tool_run.tool_name} ({tool_run.status}): "
                f"input={json.dumps(input_data, sort_keys=True)[:COMPACTION_TOOL_INPUT_PREVIEW_CHARS]}"
            )
    return "\n".join(lines)


async def compact_if_needed(
    store: RuntimeStore,
    session: SessionRecord,
    client: BaseLLMClient | None = None,
    *,
    provider_name: str | None = None,
) -> dict | None:
    """Compact oldest turns when active tokens exceed the session's context window.

    When `client` is provided, attempt an LLM-generated summary using the
    provider's `summarizer_model` override (falls back to the heuristic
    on any error). Without a client, the heuristic summary is used
    directly — keeps the CLI and offline tests working without hitting
    the network.

    Returns a dict describing the compaction (summary turn id, source turn
    ids, token counts, summary source) when one occurs, else None.
    """
    if not session.context_window:
        return None
    active_tokens = estimate_active_tokens(store, session.id)
    if active_tokens <= session.context_window:
        return None
    policy = RetentionPolicy.for_context_window(session.context_window)
    transcript = store.get_transcript(session.id)
    active_turns = [t for t in transcript.turns if not t.compacted and t.role in {"user", "assistant"}]
    source_turns = _select_source_turns(
        active_turns,
        transcript.tool_runs_by_turn,
        transcript.parts_by_turn,
        policy,
    )
    if not source_turns:
        return None
    summary_text, summary_source = await _build_summary(
        client,
        provider_name=provider_name or session.provider,
        source_turns=source_turns,
        tool_runs_by_turn=transcript.tool_runs_by_turn,
    )
    summary = store.record_compaction(
        session.id,
        summary_text,
        [turn.id for turn in source_turns],
    )
    _compact_old_tool_runs(store, session.id, policy)
    return {
        "summary_turn_id": summary.summary_turn_id,
        "source_turn_ids": summary.source_turn_ids,
        "active_tokens_before": active_tokens,
        "context_window": session.context_window,
        "recent_raw_turns": policy.recent_raw_turns,
        "recent_raw_tool_runs": policy.recent_raw_tool_runs,
        "retention_budget_tokens": policy.retention_budget_tokens,
        "source_turn_count": len(source_turns),
        "kept_turn_count": len(active_turns) - len(source_turns),
        "summary_token_count": count_text_tokens(summary_text),
        "summary_source": summary_source,
    }


async def _build_summary(
    client: BaseLLMClient | None,
    *,
    provider_name: str | None,
    source_turns: list[TurnRecord],
    tool_runs_by_turn: dict[str, list[ToolRunRecord]],
) -> tuple[str, str]:
    """Return (summary_text, summary_source) where source is 'llm' or 'heuristic'."""
    if client is None:
        return _heuristic_summary(source_turns, tool_runs_by_turn), "heuristic"
    summarizer_model: str | None = None
    if provider_name:
        try:
            summarizer_model = get_provider(provider_name).summarizer_model
        except KeyError:
            summarizer_model = None
    try:
        summary_text = await summarize_for_compaction(
            client,
            summarizer_model=summarizer_model,
            source_turns=source_turns,
            tool_runs_by_turn=tool_runs_by_turn,
        )
        return summary_text, "llm"
    except Exception as exc:
        logger.warning(
            "LLM compaction summary failed (%s) — falling back to heuristic",
            exc,
        )
        return _heuristic_summary(source_turns, tool_runs_by_turn), "heuristic"
