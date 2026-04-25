"""Pydantic wire-format models for CSV export endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExportInfo(BaseModel):
    id: str
    filename: str
    title: str
    row_count: int
    columns: list[str]
    file_size: int
    created_at: str
    updated_at: str
    download_url: str
    source_session_id: str | None = None


class ExportDetail(ExportInfo):
    sql: str
    preview_rows: list[dict]
    preview_truncated: bool


class ExportUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class NewSessionFromExportRequest(BaseModel):
    provider: str | None = Field(None, description="LLM provider for the new session")
    model: str | None = Field(None, description="Model override for the new session")


class NewSessionFromExportResponse(BaseModel):
    conversation_id: str
