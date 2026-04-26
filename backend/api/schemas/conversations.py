"""Pydantic wire-format models for conversation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.data import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    SessionListEntry,
    SessionTranscript,
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

    @classmethod
    def from_row(cls, item: SessionListEntry) -> "ConversationInfo":
        return cls(
            id=item.id,
            message_count=item.turn_count,
            title=item.title,
            provider=item.provider,
            model=item.model,
            updated_at=item.updated_at,
            pinned_at=item.pinned_at,
            source_csv_id=item.source_csv_id,
        )


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

    @classmethod
    def from_transcript(
        cls, conversation_id: str, transcript: SessionTranscript
    ) -> "ConversationTranscriptResponse":
        parts = [p for records in transcript.parts_by_turn.values() for p in records]
        tool_runs = [r for runs in transcript.tool_runs_by_turn.values() for r in runs]
        return cls(
            session_id=conversation_id,
            title=transcript.session.title,
            provider=transcript.session.provider,
            model=transcript.session.model,
            updated_at=transcript.session.updated_at,
            turns=list(transcript.turns),
            parts=parts,
            tool_runs=tool_runs,
            summaries=list(transcript.summaries),
        )
