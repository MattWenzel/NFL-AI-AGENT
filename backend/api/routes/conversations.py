"""Conversation CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.domain.auth.types import AuthenticatedUser
from backend.server.csrf import verify_csrf
from backend.api.dependencies import get_conversation_service, get_current_user
from backend.api.schemas.common import OkResponse
from backend.api.schemas.conversations import (
    ConversationInfo,
    ConversationTranscriptResponse,
    ConversationUpdate,
)
from backend.application.conversations import ConversationService
from backend.application.conversations import ConversationNotFoundError

# CSRF dep skips GET/HEAD/OPTIONS internally, so the list + transcript
# endpoints are unaffected; the PATCH + DELETE routes get protection.
router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_csrf)])


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    rows = await service.list_conversations(user.id)
    return [ConversationInfo.from_row(item) for item in rows]


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        transcript = await service.get_transcript(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ConversationTranscriptResponse.from_transcript(conversation_id, transcript)


@router.patch("/conversations/{conversation_id}", response_model=ConversationInfo)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide title and/or pinned")
    try:
        entry = await service.update_conversation(
            conversation_id,
            user_id=user.id,
            title=body.title,
            pinned=body.pinned,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ConversationInfo.from_row(entry)


@router.delete("/conversations/{conversation_id}", response_model=OkResponse)
async def delete_conversation(
    conversation_id: str,
    service: ConversationService = Depends(get_conversation_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> OkResponse:
    try:
        await service.delete_conversation(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return OkResponse()
