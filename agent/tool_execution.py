"""Tool execution coordinator for runtime tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from agent.persistence import RuntimePersistence
from storage import RuntimeStore, TurnRecord

logger = logging.getLogger(__name__)


class ToolExecutor(Protocol):
    async def __call__(self, tool_name: str, tool_input: dict, *, ctx: dict | None = None) -> dict: ...


@dataclass(frozen=True)
class ToolExecutionResult:
    status: Literal["completed", "error"]
    content: str
    error: str | None = None
    hint: str | None = None
    duration_ms: int | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"


@dataclass
class ToolExecutionService:
    store: RuntimeStore
    persistence: RuntimePersistence
    execute_tool: ToolExecutor

    async def execute_many(
        self,
        session_id: str,
        assistant_turn: TurnRecord,
        tool_runs: list,
    ) -> list[ToolExecutionResult]:
        return await asyncio.gather(
            *(self.execute_one(session_id, assistant_turn, tool_run) for tool_run in tool_runs)
        )

    async def execute_one(
        self,
        session_id: str,
        assistant_turn: TurnRecord,
        tool_run,
    ) -> ToolExecutionResult:
        await self.persistence.begin_tool_execution(
            session_id,
            assistant_turn.id,
            tool_run.id,
            tool_run.tool_name,
        )
        ctx = {
            "register_export": lambda meta: self.store.register_export(
                **meta,
                source_session_id=session_id,
                source_tool_run_id=tool_run.id,
            ),
        }
        raw_result = await self.execute_tool(tool_run.tool_name, tool_run.input, ctx=ctx)
        result = ToolExecutionResult(
            status="completed" if raw_result["status"] == "completed" else "error",
            content=raw_result["content"],
            error=raw_result.get("error"),
            hint=raw_result.get("hint"),
            duration_ms=raw_result.get("duration_ms"),
        )
        await self.persistence.complete_tool_execution(
            session_id,
            assistant_turn.id,
            tool_run.id,
            tool_run.tool_name,
            result_content=result.content,
            status=result.status,
            error=result.error,
            hint=result.hint,
            duration_ms=result.duration_ms,
        )
        return result
