"""Conversation CRUD: list, transcript, delete.

Thin facade over `RuntimeStore`. The transcript endpoint does the
dict-shaping for nested records (turns, parts, tool_runs, summaries)
since pydantic doesn't model those directly.

All endpoints require auth and scope queries to the caller's user_id so
one user can't read/mutate another user's conversations.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth.primitives import AuthenticatedUser, get_current_user
from server.dependencies import get_store
from server.serializers.conversations import conversation_info_from_row, transcript_response
from server.schemas.conversations import ConversationInfo, ConversationTranscriptResponse, ConversationUpdate
from storage import RuntimeStore

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all active conversations for the current user."""
    return [conversation_info_from_row(item) for item in store.list_sessions(user_id=user.id)]


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: str,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return the persisted transcript with turns, tool runs, and compaction summaries."""
    # Ownership check up-front — same 404 whether it doesn't exist or belongs to someone else.
    if store.get_session(conversation_id, user_id=user.id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        transcript = store.get_transcript(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return transcript_response(conversation_id, transcript)


@router.patch("/conversations/{conversation_id}", response_model=ConversationInfo)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Rename and/or pin a conversation."""
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="Provide title and/or pinned")
    session = store.get_session(conversation_id, user_id=user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        session.title = body.title.strip()
        store.update_session(session)
    if body.pinned is not None:
        session = store.set_session_pinned(conversation_id, body.pinned, user_id=user.id) or session
    entry = next(
        (s for s in store.list_sessions(user_id=user.id) if s["id"] == conversation_id),
        None,
    )
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


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    store: RuntimeStore = Depends(get_store),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a conversation owned by the current user."""
    if store.delete_session(conversation_id, user_id=user.id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Conversation not found")
