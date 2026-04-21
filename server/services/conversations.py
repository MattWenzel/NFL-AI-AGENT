"""Application service for conversation APIs."""

from __future__ import annotations

from dataclasses import dataclass

from server.repositories import ConversationListEntry, ConversationRepository
from server.schemas.conversations import ConversationInfo, ConversationTranscriptResponse
from storage import RuntimeStore, SessionTranscript, safe_load_tool_input


def _conversation_info_from_row(item: ConversationListEntry) -> ConversationInfo:
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


def _transcript_response(conversation_id: str, transcript: SessionTranscript) -> ConversationTranscriptResponse:
    tool_runs = []
    for turn_id, runs in transcript.tool_runs_by_turn.items():
        for run in runs:
            tool_runs.append({
                "id": run.id,
                "turn_id": turn_id,
                "tool_name": run.tool_name,
                "status": run.status,
                "input": safe_load_tool_input(run.input_json, tool_run_id=run.id),
                "result": run.result_text,
                "error": run.error_text,
                "hint": run.hint,
                "duration_ms": run.duration_ms,
                "compacted": run.compacted,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
            })
    parts = []
    for turn_id, records in transcript.parts_by_turn.items():
        for part in records:
            parts.append({
                "id": part.id,
                "turn_id": turn_id,
                "kind": part.kind,
                "order_index": part.order_index,
                "content": part.content,
                "name": part.name,
                "tool_run_id": part.tool_run_id,
                "created_at": part.created_at,
            })
    return ConversationTranscriptResponse(
        session_id=conversation_id,
        title=transcript.session.title,
        provider=transcript.session.provider,
        model=transcript.session.model,
        updated_at=transcript.session.updated_at,
        turns=[
            {
                "id": turn.id,
                "role": turn.role,
                "status": turn.status,
                "text": turn.text,
                "compacted": turn.compacted,
                "error": turn.error,
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "created_at": turn.created_at,
                "updated_at": turn.updated_at,
            }
            for turn in transcript.turns
        ],
        parts=parts,
        tool_runs=tool_runs,
        summaries=[
            {
                "id": summary.id,
                "summary_turn_id": summary.summary_turn_id,
                "source_turn_ids": summary.source_turn_ids,
                "created_at": summary.created_at,
            }
            for summary in transcript.summaries
        ],
    )


class ConversationServiceError(Exception):
    pass


class ConversationNotFoundError(ConversationServiceError):
    pass


@dataclass
class ConversationApplicationService:
    store: RuntimeStore
    conversations: ConversationRepository  # slim, for list mapping only

    @staticmethod
    def _fallback_entry(session) -> ConversationListEntry:
        return ConversationListEntry(
            id=session.id,
            turn_count=0,
            title=session.title or "New conversation",
            provider=session.provider,
            model=session.model,
            updated_at=session.updated_at,
            pinned_at=session.pinned_at,
            source_csv_id=session.source_csv_id,
        )

    async def list_conversations(self, user_id: int) -> list[ConversationInfo]:
        rows = await self.conversations.list_sessions(user_id=user_id)
        return [_conversation_info_from_row(item) for item in rows]

    async def get_transcript(self, conversation_id: str, user_id: int) -> ConversationTranscriptResponse:
        if await self.store.get_session_async(conversation_id, user_id=user_id) is None:
            raise ConversationNotFoundError("Conversation not found")
        try:
            transcript = await self.store.get_transcript_async(conversation_id)
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
        session = await self.store.get_session_async(conversation_id, user_id=user_id)
        if session is None:
            raise ConversationNotFoundError("Conversation not found")
        if title is not None:
            session.title = title.strip()
            await self.store.update_session_async(session)
        if pinned is not None:
            session = await self.store.set_session_pinned_async(
                conversation_id,
                pinned,
                user_id=user_id,
            ) or session
        entry = await self.conversations.get_session_list_entry(
            conversation_id,
            user_id=user_id,
        )
        if entry is None:
            entry = self._fallback_entry(session)
        return _conversation_info_from_row(entry)

    async def delete_conversation(self, conversation_id: str, user_id: int) -> None:
        if not await self.store.delete_session_async(conversation_id, user_id=user_id):
            raise ConversationNotFoundError("Conversation not found")
