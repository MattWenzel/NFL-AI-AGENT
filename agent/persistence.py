"""Explicit runtime persistence boundary over RuntimeStore.

Owns the write-side runtime semantics — creating turns, recording tool
calls, marking tool-run lifecycle transitions. Pure transforms like
assembling the wire message sequence live in `agent.message_builder`,
not here.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.runtime_repositories import RuntimeConversationRepository
from storage import SessionRecord, ToolRunRecord, TurnRecord


@dataclass
class RuntimePersistence:
    """Async-facing persistence interface for runtime hot-path operations."""

    conversations: RuntimeConversationRepository

    async def create_user_turn(self, session_id: str, text: str) -> TurnRecord:
        return await self.conversations.create_turn(
            session_id,
            "user",
            text=text,
            status="completed",
        )

    async def open_assistant_turn(self, session_id: str) -> TurnRecord:
        return await self.conversations.create_turn(
            session_id,
            "assistant",
            status="running",
        )

    async def update_session_metadata(
        self,
        session: SessionRecord,
        *,
        provider_name: str,
        model: str,
        title_preview_chars: int,
        user_text: str,
    ) -> None:
        if not session.title:
            session.title = user_text[:title_preview_chars]
        session.provider = provider_name
        session.model = model
        await self.conversations.update_session(session)

    async def append_assistant_text(self, session_id: str, turn_id: str, text: str) -> None:
        await self.conversations.append_assistant_text(session_id, turn_id, text)

    async def record_tool_call(
        self,
        session_id: str,
        turn_id: str,
        *,
        tool_name: str,
        input_data: dict,
        raw_input_text: str | None,
        tool_call_json: str,
    ) -> ToolRunRecord:
        tool_run = await self.conversations.create_tool_run(
            session_id,
            turn_id,
            tool_name,
            input_data,
            status="pending",
            raw_input_text=raw_input_text,
        )
        await self.conversations.add_part(
            session_id,
            turn_id,
            "tool_call",
            tool_call_json,
            name=tool_name,
            tool_run_id=tool_run.id,
        )
        return tool_run

    async def update_turn(self, turn_id: str, **changes) -> TurnRecord:
        return await self.conversations.update_turn(turn_id, **changes)

    async def get_turn(self, turn_id: str) -> TurnRecord | None:
        return await self.conversations.get_turn(turn_id)

    async def get_tool_run(self, tool_run_id: str) -> ToolRunRecord | None:
        return await self.conversations.get_tool_run(tool_run_id)

    async def begin_tool_execution(self, session_id: str, turn_id: str, tool_run_id: str, tool_name: str) -> None:
        await self.conversations.update_tool_run(tool_run_id, status="running")
        await self.conversations.add_part(
            session_id,
            turn_id,
            "tool_status",
            "running",
            name=tool_name,
            tool_run_id=tool_run_id,
        )

    async def complete_tool_execution(
        self,
        session_id: str,
        turn_id: str,
        tool_run_id: str,
        tool_name: str,
        *,
        result_content: str,
        status: str,
        error_text: str | None,
        hint: str | None,
        duration_ms: int | None,
    ) -> None:
        await self.conversations.update_tool_run(
            tool_run_id,
            status=status,
            result_text=result_content,
            error_text=error_text,
            hint=hint,
            duration_ms=duration_ms,
        )
        await self.conversations.add_part(
            session_id,
            turn_id,
            "tool_result",
            result_content,
            name=tool_name,
            tool_run_id=tool_run_id,
        )

    async def interrupt_turn(self, turn_id: str, error: str) -> None:
        await self.conversations.update_turn(turn_id, status="interrupted", error=error)

    async def interrupt_tool_run(self, tool_run_id: str, error_text: str) -> None:
        await self.conversations.update_tool_run(
            tool_run_id,
            status="interrupted",
            error_text=error_text,
        )
