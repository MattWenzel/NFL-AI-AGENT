"""Runtime-side repository bundle over the sqlite runtime store."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from storage import RuntimeStore, SessionRecord


@dataclass(frozen=True)
class RuntimeConversationRepository:
    _store: RuntimeStore

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._store.lock(session_id)

    async def get_or_create_session_async(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ) -> SessionRecord:
        return await self._store.get_or_create_session_async(
            session_id,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )

    async def create_turn(
        self,
        session_id: str,
        role: str,
        *,
        text: str = "",
        status: str = "completed",
    ):
        return await self._store.create_turn_async(session_id, role, text=text, status=status)

    async def update_session(self, session: SessionRecord) -> None:
        await self._store.update_session_async(session)

    async def append_assistant_text(self, session_id: str, turn_id: str, text: str) -> None:
        await self._store.append_assistant_text_async(session_id, turn_id, text)

    async def create_tool_run(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        input_data: dict,
        *,
        status: str = "pending",
        raw_input_text: str | None = None,
    ):
        return await self._store.create_tool_run_async(
            session_id,
            turn_id,
            tool_name,
            input_data,
            status=status,
            raw_input_text=raw_input_text,
        )

    async def add_part(
        self,
        session_id: str,
        turn_id: str,
        kind: str,
        content: str,
        *,
        name: str | None = None,
        tool_run_id: str | None = None,
    ):
        return await self._store.add_part_async(
            session_id,
            turn_id,
            kind,
            content,
            name=name,
            tool_run_id=tool_run_id,
        )

    async def update_turn(self, turn_id: str, **changes):
        return await self._store.update_turn_async(turn_id, **changes)

    async def get_turn(self, turn_id: str):
        return await self._store.get_turn_async(turn_id)

    async def get_tool_run(self, tool_run_id: str):
        return await self._store.get_tool_run_async(tool_run_id)

    async def update_tool_run(self, tool_run_id: str, **changes):
        return await self._store.update_tool_run_async(tool_run_id, **changes)

    async def get_transcript(self, session_id: str):
        return await self._store.get_transcript_async(session_id)

    async def record_compaction(
        self,
        session_id: str,
        summary_text: str,
        source_turn_ids: list[str],
    ):
        return await self._store.record_compaction_async(
            session_id,
            summary_text,
            source_turn_ids,
        )


@dataclass(frozen=True)
class RuntimeExportRegistry:
    _store: RuntimeStore

    def register_export(
        self,
        *,
        filename: str,
        title: str,
        sql: str,
        row_count: int,
        columns: list[str],
        file_size: int,
        source_session_id: str | None,
        source_tool_run_id: str | None,
    ):
        return self._store.register_export(
            filename=filename,
            title=title,
            sql=sql,
            row_count=row_count,
            columns=columns,
            file_size=file_size,
            source_session_id=source_session_id,
            source_tool_run_id=source_tool_run_id,
        )


@dataclass(frozen=True)
class RuntimeRepositoryBundle:
    conversations: RuntimeConversationRepository
    exports: RuntimeExportRegistry

    @classmethod
    def from_store(cls, store: RuntimeStore) -> "RuntimeRepositoryBundle":
        return cls(
            conversations=RuntimeConversationRepository(store),
            exports=RuntimeExportRegistry(store),
        )
