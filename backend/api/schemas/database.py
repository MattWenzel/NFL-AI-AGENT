"""Wire schemas for the Database browser tab.

Mirror the SQL sandbox's `SQLResult` (columns + rows + truncated flag) and
provide a small "save these rows as a Report" payload that re-uses the
table-chat infrastructure on the backend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TableColumn(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    name: str
    columns: list[TableColumn]


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20_000)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool


class SaveAsReportRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20_000)
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool = False
    title: str | None = Field(None, min_length=1, max_length=200)


class SaveAsReportResponse(BaseModel):
    conversation_id: str


__all__ = [
    "QueryRequest",
    "QueryResponse",
    "SaveAsReportRequest",
    "SaveAsReportResponse",
    "TableColumn",
    "TableInfo",
]
