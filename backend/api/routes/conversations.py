"""Conversation CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.api.dependencies import get_conversation_service, get_current_user
from backend.api.schemas.conversations import (
    ConversationInfo,
    ConversationTranscriptResponse,
    ConversationUpdate,
)
from backend.data import SessionListEntry, SessionTranscript
from backend.application.conversations import ConversationService
from backend.application.conversations import ConversationNotFoundError

# CSRF dep skips GET/HEAD/OPTIONS internally, so the list + transcript
# endpoints are unaffected; the PATCH + DELETE routes get protection.
router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_csrf)])


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
    conversation_id: str,
    transcript: SessionTranscript,
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


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    rows = await service.list_conversations(user.id)
    return [_conversation_info_from_row(item) for item in rows]


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        transcript = await service.get_transcript(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _transcript_response(conversation_id, transcript)


@router.patch("/conversations/{conversation_id}", response_model=ConversationInfo)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="Provide title and/or pinned")
    try:
        entry = await service.update_conversation(
            conversation_id,
            user_id=user.id,
            title=body.title,
            pinned=body.pinned,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _conversation_info_from_row(entry)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        await service.delete_conversation(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "deleted"}
