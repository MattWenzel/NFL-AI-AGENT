"""Internal typed results for export application services."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportSummary:
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


@dataclass(frozen=True)
class ExportDetailRecord:
    id: str
    filename: str
    title: str
    row_count: int
    columns: list[str]
    file_size: int
    created_at: str
    updated_at: str
    download_url: str
    source_session_id: str | None
    sql: str
    preview_rows: list[dict]
    preview_truncated: bool


@dataclass(frozen=True)
class NewSessionFromExportResult:
    conversation_id: str
