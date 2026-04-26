"""CSV export registry CRUD."""

from __future__ import annotations

from sqlalchemy import delete, select, update

from backend.data.models import ExportRecord, SessionRecord, new_id, utcnow


class ExportsMixin:
    """CRUD for the exports table (CSV library)."""

    async def register_export(
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
    ) -> ExportRecord:
        now = utcnow()
        owning_user_id: int | None = None
        if source_session_id:
            sess = await self.get_session(source_session_id)
            if sess is not None:
                owning_user_id = sess.user_id
        record = ExportRecord(
            id=new_id(),
            filename=filename,
            title=title,
            sql=sql,
            row_count=row_count,
            columns=list(columns),
            file_size=file_size,
            source_session_id=source_session_id,
            source_tool_run_id=source_tool_run_id,
            user_id=owning_user_id,
            created_at=now,
            updated_at=now,
        )
        async with self._async_session() as session:
            session.add(record)
            await session.commit()
        return record

    async def list_exports(self, *, user_id: int | None = None) -> list[ExportRecord]:
        async with self._async_session() as session:
            stmt = select(ExportRecord)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            stmt = stmt.order_by(
                ExportRecord.pinned_at.desc(), ExportRecord.created_at.desc()
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_export(
        self, export_id: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        async with self._async_session() as session:
            stmt = select(ExportRecord).where(ExportRecord.id == export_id)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_export_by_filename(
        self, filename: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        async with self._async_session() as session:
            stmt = select(ExportRecord).where(ExportRecord.filename == filename)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_export_title(
        self, export_id: str, title: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        now = utcnow()
        async with self._async_session() as session:
            stmt = update(ExportRecord).where(ExportRecord.id == export_id)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            stmt = stmt.values(title=title, updated_at=now)
            result = await session.execute(stmt)
            await session.commit()
            if (result.rowcount or 0) == 0:
                return None
        return await self.get_export(export_id, user_id=user_id)

    async def set_export_pinned(
        self, export_id: str, pinned: bool, *, user_id: int | None = None
    ) -> ExportRecord | None:
        pinned_at = utcnow() if pinned else None
        async with self._async_session() as session:
            stmt = update(ExportRecord).where(ExportRecord.id == export_id)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            stmt = stmt.values(pinned_at=pinned_at)
            result = await session.execute(stmt)
            await session.commit()
            if (result.rowcount or 0) == 0:
                return None
        return await self.get_export(export_id, user_id=user_id)

    async def delete_export(
        self, export_id: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        """Remove the registry row. Caller is responsible for unlinking the
        on-disk file; returning the record so the caller knows the filename."""
        record = await self.get_export(export_id, user_id=user_id)
        if record is None:
            return None
        async with self._async_session() as session:
            stmt = delete(ExportRecord).where(ExportRecord.id == export_id)
            if user_id is not None:
                stmt = stmt.where(ExportRecord.user_id == user_id)
            await session.execute(stmt)
            await session.commit()
        return record
