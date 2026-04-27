"""Pydantic wire-format models for table-view chat endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.api.schemas.conversations import ConversationInfo, ConversationTranscriptResponse
from backend.application.tables import TableChatTranscript
from backend.data import TableStateRecord


class TableChatCreate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = Field(None, description="LLM provider for the new table chat")
    model: str | None = Field(None, description="Model override for the new table chat")


class TableState(BaseModel):
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool
    locked: bool = False
    last_sql: str | None = None
    updated_at: str

    @classmethod
    def from_record(cls, record: TableStateRecord) -> "TableState":
        return cls(
            columns=list(record.columns),
            rows=list(record.rows),
            row_count=record.row_count,
            truncated=bool(record.truncated),
            locked=bool(record.locked),
            last_sql=record.last_sql,
            updated_at=record.updated_at,
        )


class TableLockUpdate(BaseModel):
    locked: bool


class TableChatResponse(BaseModel):
    """Combined transcript + live table for the table-chat view."""
    conversation: ConversationTranscriptResponse
    table: TableState | None = None

    @classmethod
    def from_transcript(
        cls, conversation_id: str, payload: TableChatTranscript
    ) -> "TableChatResponse":
        return cls(
            conversation=ConversationTranscriptResponse.from_transcript(
                conversation_id, payload.transcript
            ),
            table=TableState.from_record(payload.table) if payload.table else None,
        )


class TableChatSaveRequest(BaseModel):
    title: str | None = Field(
        None, min_length=1, max_length=200,
        description="Override the title used for the saved Report. Defaults to the chat's title.",
    )


__all__ = [
    "ConversationInfo",  # re-export so tables routes can return ConversationInfo for list/patch
    "TableChatCreate",
    "TableChatResponse",
    "TableChatSaveRequest",
    "TableLockUpdate",
    "TableState",
]
