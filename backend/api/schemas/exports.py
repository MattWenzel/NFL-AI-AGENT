"""Pydantic wire-format models for CSV export endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.data import ExportRecord


class ExportInfo(BaseModel):
    id: str
    filename: str
    title: str
    row_count: int
    columns: list[str]
    file_size: int
    created_at: str
    updated_at: str
    pinned_at: str | None = None
    download_url: str
    source_session_id: str | None = None

    @classmethod
    def from_record(cls, record: ExportRecord) -> "ExportInfo":
        return cls(
            id=record.id,
            filename=record.filename,
            title=record.title,
            row_count=record.row_count,
            columns=list(record.columns),
            file_size=record.file_size,
            created_at=record.created_at,
            updated_at=record.updated_at,
            pinned_at=record.pinned_at,
            download_url=f"/exports/{record.filename}",
            source_session_id=record.source_session_id,
        )


class ExportDetail(ExportInfo):
    sql: str
    preview_rows: list[dict]
    preview_truncated: bool

    @classmethod
    def from_record(  # type: ignore[override]
        cls,
        record: ExportRecord,
        *,
        preview_rows: list[dict],
        preview_truncated: bool,
    ) -> "ExportDetail":
        info = ExportInfo.from_record(record)
        return cls(
            **info.model_dump(),
            sql=record.sql,
            preview_rows=preview_rows,
            preview_truncated=preview_truncated,
        )


class ExportUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    pinned: bool | None = Field(None, description="Pin or unpin this export")


class NewSessionFromExportRequest(BaseModel):
    provider: str | None = Field(None, description="LLM provider for the new session")
    model: str | None = Field(None, description="Model override for the new session")


class NewSessionFromExportResponse(BaseModel):
    conversation_id: str
