"""Table-state repository: the live table for a table-chat session.

One row per session (PK = session_id). Replaced wholesale on every
`set_table` tool call. ON DELETE CASCADE on the session FK keeps the
row from outliving its session.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from backend.data.models import TableStateRecord, utcnow


class TableStatesMixin:
    """Async CRUD for `table_states`."""

    async def get_table_state(self, session_id: str) -> TableStateRecord | None:
        async with self._async_session() as session:
            result = await session.execute(
                select(TableStateRecord).where(TableStateRecord.session_id == session_id)
            )
            return result.scalar_one_or_none()

    async def upsert_table_state(
        self,
        session_id: str,
        *,
        columns: list[str],
        rows: list[dict],
        last_sql: str | None,
        row_count: int,
        truncated: bool,
    ) -> TableStateRecord:
        now = utcnow()
        async with self._async_session() as session:
            existing = (
                await session.execute(
                    select(TableStateRecord).where(
                        TableStateRecord.session_id == session_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                record = TableStateRecord(
                    session_id=session_id,
                    columns=columns,
                    rows=rows,
                    last_sql=last_sql,
                    row_count=row_count,
                    truncated=truncated,
                    updated_at=now,
                )
                session.add(record)
            else:
                existing.columns = columns
                existing.rows = rows
                existing.last_sql = last_sql
                existing.row_count = row_count
                existing.truncated = truncated
                existing.updated_at = now
                record = existing
            await session.commit()
            await session.refresh(record)
            return record

    async def set_table_locked(self, session_id: str, locked: bool) -> bool:
        """Flip the lock flag on an existing table state row.

        Returns True if a row was updated, False if no table state exists yet.
        Doesn't touch `updated_at` — locking isn't a content change.
        """
        async with self._async_session() as session:
            existing = (
                await session.execute(
                    select(TableStateRecord).where(
                        TableStateRecord.session_id == session_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            existing.locked = bool(locked)
            await session.commit()
            return True

    async def delete_table_state(self, session_id: str) -> bool:
        async with self._async_session() as session:
            result = await session.execute(
                delete(TableStateRecord).where(
                    TableStateRecord.session_id == session_id
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0
