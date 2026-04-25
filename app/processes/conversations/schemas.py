"""Conversation list / transcript / update shapes.

Nested transcript types (Turn, AssistantPart, ToolRun, CompactionSummary)
come straight from `storage.models` — they're the domain objects. Their
`session_id` field is excluded from serialization (redundant inside a
transcript response that already identifies the session).
"""

from pydantic import BaseModel, Field

from core.persistence import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    ToolRunRecord,
    TurnRecord,
)


class ConversationInfo(BaseModel):
    id: str
    message_count: int
    title: str
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    pinned_at: str | None = None
    source_csv_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200, description="New conversation title")
    pinned: bool | None = Field(None, description="Pin or unpin this conversation")


class ConversationTranscriptResponse(BaseModel):
    session_id: str
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    updated_at: str | None = None
    turns: list[TurnRecord]
    parts: list[AssistantPartRecord]
    tool_runs: list[ToolRunRecord]
    summaries: list[CompactionSummaryRecord]
