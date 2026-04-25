"""Build provider-facing message sequences from persisted transcripts."""

from __future__ import annotations

import json

from backend.providers.types import Message, ToolUseEvent
from backend.storage import SessionTranscript
from backend.storage.models import wrap_summaries_for_prompt


def build_model_messages(transcript: SessionTranscript) -> list[Message]:
    """Build the wire message sequence for the next model call.

    Summary turns are always emitted first, regardless of when they were
    created, so a compaction that fires mid-session still leaves the latest
    user message at the tail of the prompt.
    """
    messages: list[Message] = []

    summary_turns = [
        turn for turn in transcript.turns
        if not turn.compacted and turn.role == "summary"
    ]
    if summary_turns:
        messages.append(Message(
            role="assistant",
            text=wrap_summaries_for_prompt(summary_turns),
        ))

    for turn in transcript.turns:
        if turn.compacted or turn.role == "summary":
            continue
        if turn.role == "user":
            messages.append(Message(role="user", text=turn.text))
            continue
        if turn.role != "assistant":
            continue

        parts = transcript.parts_by_turn.get(turn.id, [])
        text = turn.text
        if not text:
            text = "".join(part.content for part in parts if part.kind == "text")
        text = (text or "").rstrip()

        tool_calls = [
            ToolUseEvent(
                id=tool_run.id,
                name=tool_run.tool_name,
                input=tool_run.input,
            )
            for tool_run in transcript.tool_runs_by_turn.get(turn.id, [])
        ]
        if text or tool_calls:
            messages.append(Message(
                role="assistant",
                text=text or None,
                tool_calls=tool_calls or None,
            ))
        for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
            if tool_run.compacted:
                continue
            content = tool_run.result or json.dumps(
                {
                    "status": tool_run.status,
                    "error": tool_run.error or "Tool run incomplete",
                    "hint": tool_run.hint,
                },
                separators=(",", ":"),
            )
            messages.append(Message(
                role="tool_result",
                tool_use_id=tool_run.id,
                tool_content=content,
            ))
    return messages
