"""CSV export registry CRUD.

Mixed into `RuntimeStore` — expects `self._connect()` and a companion
`get_session` from `TranscriptsMixin` for owner discovery on register.
"""

from __future__ import annotations

import json

from storage._rows import row_to_export
from storage.records import ExportRecord, new_id, utcnow


class ExportsMixin:
    """CRUD for the exports table (CSV library)."""

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
    ) -> ExportRecord:
        now = utcnow()
        record = ExportRecord(
            id=new_id(),
            filename=filename,
            title=title,
            sql=sql,
            row_count=row_count,
            columns_json=json.dumps(columns),
            file_size=file_size,
            source_session_id=source_session_id,
            source_tool_run_id=source_tool_run_id,
            created_at=now,
            updated_at=now,
        )
        owning_user_id: int | None = None
        if source_session_id:
            sess = self.get_session(source_session_id)
            if sess is not None:
                owning_user_id = sess.user_id
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    id, filename, title, sql, row_count, columns_json, file_size,
                    source_session_id, source_tool_run_id, created_at, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.filename,
                    record.title,
                    record.sql,
                    record.row_count,
                    record.columns_json,
                    record.file_size,
                    record.source_session_id,
                    record.source_tool_run_id,
                    record.created_at,
                    record.updated_at,
                    owning_user_id,
                ),
            )
        return record

    def list_exports(self, *, user_id: int | None = None) -> list[ExportRecord]:
        with self._connect() as conn:
            if user_id is not None:
                rows = conn.execute(
                    "SELECT * FROM exports WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM exports ORDER BY created_at DESC"
                ).fetchall()
        return [row_to_export(r) for r in rows]

    def get_export(self, export_id: str, *, user_id: int | None = None) -> ExportRecord | None:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM exports WHERE id = ? AND user_id = ?",
                    (export_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
        return row_to_export(row) if row else None

    def get_export_by_filename(self, filename: str, *, user_id: int | None = None) -> ExportRecord | None:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM exports WHERE filename = ? AND user_id = ?",
                    (filename, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM exports WHERE filename = ?", (filename,)
                ).fetchone()
        return row_to_export(row) if row else None

    def update_export_title(
        self, export_id: str, title: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        now = utcnow()
        with self._connect() as conn:
            if user_id is not None:
                cur = conn.execute(
                    "UPDATE exports SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (title, now, export_id, user_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE exports SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, export_id),
                )
            if cur.rowcount == 0:
                return None
        return self.get_export(export_id, user_id=user_id)

    def delete_export(
        self, export_id: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        """Remove the registry row. Caller is responsible for unlinking the
        on-disk file; returning the record so the caller knows the filename."""
        record = self.get_export(export_id, user_id=user_id)
        if record is None:
            return None
        with self._connect() as conn:
            if user_id is not None:
                conn.execute(
                    "DELETE FROM exports WHERE id = ? AND user_id = ?",
                    (export_id, user_id),
                )
            else:
                conn.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        return record
