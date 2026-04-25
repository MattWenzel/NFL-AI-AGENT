"""Session lifecycle and session-list reads for `RuntimeStore`."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

from backend.lib.storage.models import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    SessionListEntry,
    SessionRecord,
    ToolRunRecord,
    TurnRecord,
    new_id,
    utcnow,
)


def _session_list_projection():
    """Build the SELECT columns for the session list projection."""

    first_user_turn = (
        select(TurnRecord.text)
        .where(
            TurnRecord.session_id == SessionRecord.id,
            TurnRecord.role == "user",
        )
        .order_by(TurnRecord.created_at)
        .limit(1)
        .correlate(SessionRecord)
        .scalar_subquery()
    )
    turn_count = (
        select(func.count())
        .select_from(TurnRecord)
        .where(TurnRecord.session_id == SessionRecord.id)
        .correlate(SessionRecord)
        .scalar_subquery()
    )
    return (
        SessionRecord.id,
        SessionRecord.updated_at,
        SessionRecord.pinned_at,
        SessionRecord.source_csv_id,
        func.coalesce(
            SessionRecord.title,
            func.substr(first_user_turn, 1, 60),
            "New conversation",
        ).label("title"),
        SessionRecord.provider,
        SessionRecord.model,
        turn_count.label("turn_count"),
    )


class SessionStoreMixin:
    """Async session CRUD and typed session-list projections."""

    async def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ) -> SessionRecord:
        existing = await self.get_session(session_id, user_id=user_id) if session_id else None
        if existing:
            changes: dict[str, object] = {}
            if provider and existing.provider != provider:
                existing.provider = provider
                changes["provider"] = provider
            if model and existing.model != model:
                existing.model = model
                changes["model"] = model
            if context_window and existing.context_window != context_window:
                existing.context_window = context_window
                changes["context_window"] = context_window
            if changes:
                await self.update_session(existing.id, **changes)
            return existing

        now = utcnow()
        record = SessionRecord(
            id=session_id or new_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def update_session(self, session_id: str, **changes) -> SessionRecord | None:
        if not changes:
            return await self.get_session(session_id)
        now = utcnow()
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(**changes, updated_at=now)
            )
            await session.commit()
        return await self.get_session(session_id)

    async def set_session_pinned(
        self, session_id: str, pinned: bool, *, user_id: int | None = None
    ) -> SessionRecord | None:
        record = await self.get_session(session_id, user_id=user_id)
        if record is None:
            return None
        record.pinned_at = utcnow() if pinned else None
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(pinned_at=record.pinned_at)
            )
            await session.commit()
        return record

    async def set_session_source_csv(
        self, session_id: str, export_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        record = await self.get_session(session_id, user_id=user_id)
        if record is None:
            return None
        record.source_csv_id = export_id
        async with self._async_session() as session:
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id == session_id)
                .values(source_csv_id=export_id)
            )
            await session.commit()
        return record

    async def get_session(
        self, session_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        if not session_id:
            return None
        async with self._async_session() as session:
            stmt = select(SessionRecord).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(self, *, user_id: int | None = None) -> list[SessionListEntry]:
        async with self._async_session() as session:
            stmt = select(*_session_list_projection())
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            stmt = stmt.order_by(SessionRecord.pinned_at.desc(), SessionRecord.updated_at.desc())
            result = await session.execute(stmt)
            return [SessionListEntry.from_row(row._mapping) for row in result.all()]

    async def get_session_list_entry(
        self, session_id: str, *, user_id: int | None = None
    ) -> SessionListEntry | None:
        async with self._async_session() as session:
            stmt = select(*_session_list_projection()).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            result = await session.execute(stmt)
            row = result.first()
            return SessionListEntry.from_row(row._mapping) if row else None

    async def delete_session(self, session_id: str, *, user_id: int | None = None) -> bool:
        async with self._async_session() as session:
            stmt = select(SessionRecord.id).where(SessionRecord.id == session_id)
            if user_id is not None:
                stmt = stmt.where(SessionRecord.user_id == user_id)
            found = await session.execute(stmt)
            if found.scalar_one_or_none() is None:
                return False
            await session.execute(
                delete(AssistantPartRecord).where(AssistantPartRecord.session_id == session_id)
            )
            await session.execute(
                delete(ToolRunRecord).where(ToolRunRecord.session_id == session_id)
            )
            await session.execute(
                delete(CompactionSummaryRecord).where(
                    CompactionSummaryRecord.session_id == session_id
                )
            )
            await session.execute(delete(TurnRecord).where(TurnRecord.session_id == session_id))
            await session.execute(delete(SessionRecord).where(SessionRecord.id == session_id))
            await session.commit()
        self._release_lock(session_id)
        return True
