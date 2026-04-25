"""Application service for CSV export library APIs."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from core.config import EXPORTS_DIR
from core.providers import get_default_provider, get_provider
from app.processes.exports.schemas import (
    ExportDetail,
    ExportInfo,
    NewSessionFromExportResponse,
)
from app.processes.exports.errors import ExportNotFoundError, ExportServiceError
from core.persistence import RuntimeStore

logger = logging.getLogger(__name__)

PREVIEW_ROW_LIMIT = 50


@dataclass
class ExportService:
    store: RuntimeStore
    exports_dir: Path = EXPORTS_DIR

    def _to_info(self, record) -> ExportInfo:
        return ExportInfo(
            id=record.id,
            filename=record.filename,
            title=record.title,
            row_count=record.row_count,
            columns=list(record.columns),
            file_size=record.file_size,
            created_at=record.created_at,
            updated_at=record.updated_at,
            download_url=f"/exports/{record.filename}",
            source_session_id=record.source_session_id,
        )

    async def list_exports(self, user_id: int) -> list[ExportInfo]:
        records = await self.store.list_exports(user_id=user_id)
        return [self._to_info(r) for r in records]

    async def get_export_detail(self, export_id: str, user_id: int) -> ExportDetail:
        record = await self.store.get_export(export_id, user_id=user_id)
        if record is None:
            raise ExportNotFoundError("CSV not found")
        preview_rows: list[dict] = []
        preview_truncated = False
        csv_path = self.exports_dir / record.filename
        if csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8", newline="") as fp:
                    reader = csv.DictReader(fp)
                    for idx, row in enumerate(reader):
                        if idx >= PREVIEW_ROW_LIMIT:
                            preview_truncated = True
                            break
                        preview_rows.append(row)
            except OSError as exc:
                logger.warning("Could not read CSV preview for %s: %s", record.filename, exc)
        info = self._to_info(record)
        return ExportDetail(
            id=info.id,
            filename=info.filename,
            title=info.title,
            row_count=info.row_count,
            columns=info.columns,
            file_size=info.file_size,
            created_at=info.created_at,
            updated_at=info.updated_at,
            download_url=info.download_url,
            source_session_id=info.source_session_id,
            sql=record.sql,
            preview_rows=preview_rows,
            preview_truncated=preview_truncated,
        )

    async def rename_export(self, export_id: str, title: str, user_id: int) -> ExportInfo:
        updated = await self.store.update_export_title(export_id, title.strip(), user_id=user_id)
        if updated is None:
            raise ExportNotFoundError("CSV not found")
        return self._to_info(updated)

    async def delete_export(self, export_id: str, user_id: int) -> None:
        record = await self.store.delete_export(export_id, user_id=user_id)
        if record is None:
            raise ExportNotFoundError("CSV not found")
        try:
            (self.exports_dir / record.filename).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not unlink CSV file %s: %s", record.filename, exc)

    async def get_download_record(self, filename: str, user_id: int):
        record = await self.store.get_export_by_filename(filename, user_id=user_id)
        if record is None:
            raise ExportNotFoundError("Export not found")
        return record

    async def create_session_from_export(
        self,
        export_id: str,
        *,
        user_id: int,
        provider_name: str | None,
        model: str | None,
    ) -> NewSessionFromExportResponse:
        record = await self.store.get_export(export_id, user_id=user_id)
        if record is None:
            raise ExportNotFoundError("CSV not found")
        resolved_provider = provider_name or get_default_provider()
        try:
            info = get_provider(resolved_provider)
        except KeyError as exc:
            raise ExportServiceError(str(exc))
        resolved_model = model or info.default_model
        session = await self.store.get_or_create_session(
            provider=resolved_provider,
            model=resolved_model,
            context_window=info.effective_context_window,
            user_id=user_id,
        )
        await self.store.update_session(session.id, title=record.title)
        await self.store.set_session_source_csv(session.id, export_id, user_id=user_id)
        columns = list(record.columns)
        summary_text = (
            f"The user has opened a saved CSV for this conversation.\n"
            f"- Title: {record.title}\n"
            f"- File: {record.filename}\n"
            f"- Row count: {record.row_count}\n"
            f"- Columns: {', '.join(columns) if columns else '(none recorded)'}\n"
            f"- Generated by this SQL:\n```sql\n{record.sql}\n```\n"
            f"Use this context for follow-up questions. You can reference the data "
            f"by re-running the SQL or variants of it; you do not have the CSV bytes directly."
        )
        await self.store.seed_summary(session.id, summary_text)
        return NewSessionFromExportResponse(conversation_id=session.id)
