"""Conversation response serialization helpers."""

from __future__ import annotations

from server.repositories import ConversationListEntry
from server.schemas.conversations import ConversationInfo, ConversationTranscriptResponse
from storage import SessionTranscript, safe_load_tool_input


def conversation_info_from_row(item: ConversationListEntry) -> ConversationInfo:
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


def transcript_response(conversation_id: str, transcript: SessionTranscript) -> ConversationTranscriptResponse:
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
