"""Tool execution coordinator for runtime tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from agent.persistence import RuntimePersistence
from agent.runtime_repositories import RuntimeExportRegistry
from storage import TurnRecord

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionService:
    exports: RuntimeExportRegistry
    persistence: RuntimePersistence
    execute_tool: object

    async def execute_many(
        self,
        session_id: str,
        assistant_turn: TurnRecord,
        tool_runs: list,
    ) -> list[dict]:
        return await asyncio.gather(
            *(self.execute_one(session_id, assistant_turn, tool_run) for tool_run in tool_runs)
        )

    async def execute_one(self, session_id: str, assistant_turn: TurnRecord, tool_run) -> dict:
        await self.persistence.begin_tool_execution(
            session_id,
            assistant_turn.id,
            tool_run.id,
            tool_run.tool_name,
        )
        try:
            tool_input = json.loads(tool_run.input_json) if tool_run.input_json else {}
        except json.JSONDecodeError as exc:
            err = f"Malformed tool input JSON: {exc}"
            logger.warning("tool_run %s has malformed input_json: %s", tool_run.id, exc)
            await self.persistence.complete_tool_execution(
                session_id,
                assistant_turn.id,
                tool_run.id,
                tool_run.tool_name,
                result_content=err,
                status="error",
                error_text=err,
                hint=None,
                duration_ms=None,
            )
            return {"status": "error", "content": err, "error": err}

        ctx = {
            "register_export": lambda meta: self.exports.register_export(
                **meta,
                source_session_id=session_id,
                source_tool_run_id=tool_run.id,
            ),
        }
        result = await self.execute_tool(tool_run.tool_name, tool_input, ctx=ctx)
        status = "completed" if result["status"] == "completed" else "error"
        await self.persistence.complete_tool_execution(
            session_id,
            assistant_turn.id,
            tool_run.id,
            tool_run.tool_name,
            result_content=result["content"],
            status=status,
            error_text=result.get("error"),
            hint=result.get("hint"),
            duration_ms=result.get("duration_ms"),
        )
        return result
