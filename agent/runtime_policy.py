"""Loop policy for the chat runtime: per-turn iteration state, compaction
trigger, doom-loop detection, and overflow retry bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.compaction import compact_if_needed
from agent.events import RuntimeEvent, RuntimeLoopError
from provider import BaseLLMClient, ToolChoice
from storage import RuntimeStore, SessionRecord


# Doom-loop detector: if the agent calls the same tool with the same input
# this many times in a row within a single user turn, raise so the caller
# can surface a clear error instead of burning the iteration budget.
# Scope is one user turn — `RuntimeLoopState.user_turn_tool_runs` resets
# when the next user message arrives, so repeating a query in a follow-up
# ("try again", "rerun that") is legitimate and won't trip this.
DOOM_LOOP_MATCH = 3


def raise_if_doom_loop(tool_runs) -> None:
    if len(tool_runs) < DOOM_LOOP_MATCH:
        return
    tail = [(r.tool_name, r.input_json) for r in tool_runs[-DOOM_LOOP_MATCH:]]
    if all(fp == tail[0] for fp in tail):
        raise RuntimeLoopError(
            f"Detected repeated tool loop on {tail[0][0]} with identical input"
        )


@dataclass
class RuntimeLoopState:
    initial_tool_choice: ToolChoice | None = None
    iterations: int = 0
    force_tool_choice_next_iter: ToolChoice | None = None
    force_overflow_compaction: bool = False
    overflow_retry_used: bool = False
    user_turn_tool_runs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.force_tool_choice_next_iter = self.initial_tool_choice

    def begin_iteration(self) -> tuple[int, ToolChoice | None]:
        self.iterations += 1
        iter_tool_choice = self.force_tool_choice_next_iter
        self.force_tool_choice_next_iter = None
        return self.iterations, iter_tool_choice

    async def compact_if_needed(
        self,
        store: RuntimeStore,
        session: SessionRecord,
        client: BaseLLMClient,
        *,
        provider_name: str,
    ) -> dict | None:
        if self.force_overflow_compaction:
            info = await compact_if_needed(
                store,
                session,
                client,
                provider_name=provider_name,
                force=True,
                retention_budget_override=session.context_window // 4,
            )
            self.force_overflow_compaction = False
            return info
        return await compact_if_needed(
            store,
            session,
            client,
            provider_name=provider_name,
        )

    def compaction_event(self, session_id: str, compaction_info: dict) -> RuntimeEvent:
        return RuntimeEvent(
            type="compaction_started",
            session_id=session_id,
            turn_id=compaction_info["summary_turn_id"],
            iterations=self.iterations,
            meta=compaction_info,
        )

    def record_tool_runs(self, tool_runs: list) -> None:
        self.user_turn_tool_runs.extend(tool_runs)
        raise_if_doom_loop(self.user_turn_tool_runs)

    def handle_overflow(self) -> bool:
        if self.overflow_retry_used:
            return False
        self.overflow_retry_used = True
        self.force_overflow_compaction = True
        return True

    def max_iterations_event(self, session_id: str, max_iterations: int) -> RuntimeEvent:
        return RuntimeEvent(
            type="runtime_error",
            session_id=session_id,
            error=f"Reached maximum tool iterations ({max_iterations})",
            iterations=max_iterations,
        )
