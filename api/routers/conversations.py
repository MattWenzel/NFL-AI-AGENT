"""Conversation CRUD: list, transcript, delete.

Thin facade over `RuntimeStore`. The transcript endpoint does the
dict-shaping for nested records (turns, parts, tool_runs, summaries)
since pydantic doesn't model those directly.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_store
from api.schemas import ConversationInfo, ConversationTranscriptResponse, ConversationUpdate
from infra.persistence.runtime_store import RuntimeStore, safe_load_tool_input

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationInfo])
async def list_conversations(store: RuntimeStore = Depends(get_store)):
    """List all active conversations."""
    return [
        ConversationInfo(
            id=item["id"],
            message_count=item["turn_count"],
            title=item["title"],
            provider=item.get("provider"),
            model=item.get("model"),
            updated_at=item.get("updated_at"),
            pinned_at=item.get("pinned_at"),
            source_csv_id=item.get("source_csv_id"),
        )
        for item in store.list_sessions()
    ]


@router.get("/conversations/{conversation_id}/transcript", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: str,
    store: RuntimeStore = Depends(get_store),
):
    """Return the persisted transcript with turns, tool runs, and compaction summaries."""
    try:
        transcript = store.get_transcript(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")

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


@router.patch("/conversations/{conversation_id}", response_model=ConversationInfo)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    store: RuntimeStore = Depends(get_store),
):
    """Rename and/or pin a conversation."""
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="Provide title and/or pinned")
    session = store.get_session(conversation_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        session.title = body.title.strip()
        store.update_session(session)
    if body.pinned is not None:
        session = store.set_session_pinned(conversation_id, body.pinned) or session
    entry = next((s for s in store.list_sessions() if s["id"] == conversation_id), None)
    return ConversationInfo(
        id=session.id,
        message_count=entry["turn_count"] if entry else 0,
        title=entry["title"] if entry else (session.title or "New conversation"),
        provider=session.provider,
        model=session.model,
        updated_at=session.updated_at,
        pinned_at=session.pinned_at,
        source_csv_id=session.source_csv_id,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    store: RuntimeStore = Depends(get_store),
):
    """Delete a conversation."""
    if store.delete_session(conversation_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Conversation not found")
