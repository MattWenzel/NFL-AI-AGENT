"""Application service for conversation APIs."""

from __future__ import annotations

from dataclasses import dataclass

from server.repositories import ConversationRepository
from server.schemas.conversations import ConversationInfo, ConversationTranscriptResponse
from server.serializers.conversations import conversation_info_from_row, transcript_response


class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass


@dataclass
class ConversationApplicationService:
    conversations: ConversationRepository

    async def list_conversations(self, user_id: int) -> list[ConversationInfo]:
        rows = await self.conversations.list_sessions(user_id=user_id)
        return [conversation_info_from_row(item) for item in rows]

    async def get_transcript(self, conversation_id: str, user_id: int) -> ConversationTranscriptResponse:
        if await self.conversations.get_session(conversation_id, user_id=user_id) is None:
            raise ConversationNotFoundError("Conversation not found")
        try:
            transcript = await self.conversations.get_transcript(conversation_id)
        except KeyError:
            raise ConversationNotFoundError("Conversation not found")
        return transcript_response(conversation_id, transcript)

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        user_id: int,
        title: str | None,
        pinned: bool | None,
    ) -> ConversationInfo:
        session = await self.conversations.get_session(conversation_id, user_id=user_id)
        if session is None:
            raise ConversationNotFoundError("Conversation not found")
        if title is not None:
            session.title = title.strip()
            await self.conversations.update_session(session)
        if pinned is not None:
            session = await self.conversations.set_session_pinned(
                conversation_id,
                pinned,
                user_id=user_id,
            ) or session
        rows = await self.conversations.list_sessions(user_id=user_id)
        entry = next((s for s in rows if s["id"] == conversation_id), None)
        if entry is None:
            entry = {
                "id": session.id,
                "turn_count": 0,
                "title": session.title or "New conversation",
                "provider": session.provider,
                "model": session.model,
                "updated_at": session.updated_at,
                "pinned_at": session.pinned_at,
                "source_csv_id": session.source_csv_id,
            }
        return conversation_info_from_row(entry)

    async def delete_conversation(self, conversation_id: str, user_id: int) -> None:
        if not await self.conversations.delete_session(conversation_id, user_id=user_id):
            raise ConversationNotFoundError("Conversation not found")
