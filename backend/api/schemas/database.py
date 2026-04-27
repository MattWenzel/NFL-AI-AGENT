"""Wire schemas for the Database browser tab.

Mirror the SQL sandbox's `SQLResult` (columns + rows + truncated flag) and
provide a small "save these rows as a Report" payload that re-uses the
table-chat infrastructure on the backend.
"""

from __future__ import annotations

from typing import Literal

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


class DbHelperToolCall(BaseModel):
    """Tool call carried inside an assistant message — wire-shaped to match
    `backend.domain.providers.types.ToolUseEvent` so the message builder
    can rehydrate without remapping fields."""

    id: str
    name: str
    input: dict = Field(default_factory=dict)


class DbHelperChatMessage(BaseModel):
    """Single entry in the helper-chat history.

    The browser holds the canonical list and POSTs it back each turn.
    Roles mirror the provider-facing `Message` type — `tool_result`
    messages carry the matched `tool_use_id` and the tool's JSON
    `content`; assistant messages carry text and/or `tool_calls`.
    """

    role: Literal["user", "assistant", "tool_result"]
    text: str | None = None
    tool_calls: list[DbHelperToolCall] | None = None
    tool_use_id: str | None = None
    content: str | None = None


class DbHelperChatRequest(BaseModel):
    messages: list[DbHelperChatMessage] = Field(..., min_length=1)
    provider: str | None = None
    model: str | None = None
    tool_choice: Literal["auto", "required", "none"] | None = None


__all__ = [
    "DbHelperChatMessage",
    "DbHelperChatRequest",
    "DbHelperToolCall",
    "QueryRequest",
    "QueryResponse",
    "SaveAsReportRequest",
    "SaveAsReportResponse",
    "TableColumn",
    "TableInfo",
]
