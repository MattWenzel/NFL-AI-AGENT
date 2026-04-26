"""Conversation feature: list / transcript / patch / delete."""

from __future__ import annotations

from dataclasses import dataclass

from backend.data import RuntimeStore, SessionListEntry, SessionTranscript


class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass


@dataclass
class ConversationService:
    store: RuntimeStore

    async def list_conversations(self, user_id: int) -> list[SessionListEntry]:
        return await self.store.list_sessions(user_id=user_id)

    async def get_transcript(
        self, conversation_id: str, user_id: int
    ) -> SessionTranscript:
        if await self.store.get_session(conversation_id, user_id=user_id) is None:
            raise ConversationNotFoundError("Conversation not found")
        try:
            return await self.store.get_transcript(conversation_id)
        except KeyError:
            raise ConversationNotFoundError("Conversation not found")

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        user_id: int,
        title: str | None,
        pinned: bool | None,
    ) -> SessionListEntry:
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
        return entry

    async def delete_conversation(self, conversation_id: str, user_id: int) -> None:
        if not await self.store.delete_session(conversation_id, user_id=user_id):
            raise ConversationNotFoundError("Conversation not found")
