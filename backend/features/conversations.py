"""Conversation process: schemas, errors, and application service."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.storage import (
    AssistantPartRecord,
    CompactionSummaryRecord,
    RuntimeStore,
    SessionListEntry,
    SessionTranscript,
    ToolRunRecord,
    TurnRecord,
)


class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass


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


def _conversation_info_from_row(item: SessionListEntry) -> ConversationInfo:
    return ConversationInfo(
        id=item.id,
        message_count=item.turn_count,
        title=item.title,
        provider=item.provider,
        model=item.model,
        updated_at=item.updated_at,
        pinned_at=item.pinned_at,
        source_csv_id=item.source_csv_id,
    )


def _transcript_response(
    conversation_id: str, transcript: SessionTranscript
) -> ConversationTranscriptResponse:
    parts = [p for records in transcript.parts_by_turn.values() for p in records]
    tool_runs = [r for runs in transcript.tool_runs_by_turn.values() for r in runs]
    return ConversationTranscriptResponse(
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


@dataclass
class ConversationService:
    store: RuntimeStore

    async def list_conversations(self, user_id: int) -> list[ConversationInfo]:
        rows = await self.store.list_sessions(user_id=user_id)
        return [_conversation_info_from_row(item) for item in rows]

    async def get_transcript(
        self, conversation_id: str, user_id: int
    ) -> ConversationTranscriptResponse:
        if await self.store.get_session(conversation_id, user_id=user_id) is None:
            raise ConversationNotFoundError("Conversation not found")
        try:
            transcript = await self.store.get_transcript(conversation_id)
        except KeyError:
            raise ConversationNotFoundError("Conversation not found")
        return _transcript_response(conversation_id, transcript)

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        user_id: int,
        title: str | None,
        pinned: bool | None,
    ) -> ConversationInfo:
        session = await self.store.get_session(conversation_id, user_id=user_id)
        if session is None:
            raise ConversationNotFoundError("Conversation not found")
        if title is not None:
            await self.store.update_session(conversation_id, title=title.strip())
        if pinned is not None:
            session = await self.store.set_session_pinned(
                conversation_id,
                pinned,
                user_id=user_id,
            ) or session
        entry = await self.store.get_session_list_entry(
            conversation_id,
            user_id=user_id,
        )
        if entry is None:
            raise ConversationNotFoundError("Conversation not found")
        return _conversation_info_from_row(entry)

    async def delete_conversation(self, conversation_id: str, user_id: int) -> None:
        if not await self.store.delete_session(conversation_id, user_id=user_id):
            raise ConversationNotFoundError("Conversation not found")
