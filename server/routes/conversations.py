"""Conversation CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from auth.primitives import AuthenticatedUser, get_current_user
from server.dependencies import get_conversation_repository
from server.repositories import ConversationRepository
from server.schemas.conversations import ConversationInfo, ConversationTranscriptResponse, ConversationUpdate
from server.services.conversations import ConversationApplicationService, ConversationNotFoundError

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ConversationApplicationService(conversations)
    return await service.list_conversations(user.id)


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: str,
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ConversationApplicationService(conversations)
    try:
        return await service.get_transcript(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/conversations/{conversation_id}", response_model=ConversationInfo)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="Provide title and/or pinned")
    service = ConversationApplicationService(conversations)
    try:
        return await service.update_conversation(
            conversation_id,
            user_id=user.id,
            title=body.title,
            pinned=body.pinned,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    conversations: ConversationRepository = Depends(get_conversation_repository),
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = ConversationApplicationService(conversations)
    try:
        await service.delete_conversation(conversation_id, user.id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "deleted"}
