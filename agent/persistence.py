"""Explicit runtime persistence boundary over RuntimeStore."""

from __future__ import annotations

from dataclasses import dataclass

from agent.message_builder import build_model_messages
from storage import RuntimeStore, SessionRecord, ToolRunRecord, TurnRecord


@dataclass
class RuntimePersistence:
    """Async-facing persistence interface for runtime hot-path operations."""

    store: RuntimeStore

    async def create_user_turn(self, session_id: str, text: str) -> TurnRecord:
        return await self.store.create_turn_async(
            session_id, "user", text=text, status="completed"
        )

    async def open_assistant_turn(self, session_id: str) -> TurnRecord:
        return await self.store.create_turn_async(
            session_id, "assistant", status="running"
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
        await self.store.update_session_async(session)

    async def append_assistant_text(self, session_id: str, turn_id: str, text: str) -> None:
        await self.store.append_assistant_text_async(session_id, turn_id, text)

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
        tool_run = await self.store.create_tool_run_async(
            session_id,
            turn_id,
            tool_name,
            input_data,
            status="pending",
            raw_input_text=raw_input_text,
        )
        await self.store.add_part_async(
            session_id,
            turn_id,
            "tool_call",
            tool_call_json,
            name=tool_name,
            tool_run_id=tool_run.id,
        )
        return tool_run

    async def update_turn(self, turn_id: str, **changes) -> TurnRecord:
        return await self.store.update_turn_async(turn_id, **changes)

    async def get_turn(self, turn_id: str) -> TurnRecord | None:
        return await self.store.get_turn_async(turn_id)

    async def get_tool_run(self, tool_run_id: str) -> ToolRunRecord | None:
        return await self.store.get_tool_run_async(tool_run_id)

    async def build_model_messages(self, session_id: str):
        transcript = await self.store.get_transcript_async(session_id)
        return build_model_messages(transcript)

    async def begin_tool_execution(self, session_id: str, turn_id: str, tool_run_id: str, tool_name: str) -> None:
        await self.store.update_tool_run_async(tool_run_id, status="running")
        await self.store.add_part_async(
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
        await self.store.update_tool_run_async(
            tool_run_id,
            status=status,
            result_text=result_content,
            error_text=error_text,
            hint=hint,
            duration_ms=duration_ms,
        )
        await self.store.add_part_async(
            session_id,
            turn_id,
            "tool_result",
            result_content,
            name=tool_name,
            tool_run_id=tool_run_id,
        )

    async def interrupt_turn(self, turn_id: str, error: str) -> None:
        await self.store.update_turn_async(turn_id, status="interrupted", error=error)

    async def interrupt_tool_run(self, tool_run_id: str, error_text: str) -> None:
        await self.store.update_tool_run_async(
            tool_run_id,
            status="interrupted",
            error_text=error_text,
        )
