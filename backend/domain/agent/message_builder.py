"""Build provider-facing message sequences from persisted transcripts."""

from __future__ import annotations

import json
from typing import Iterable

from backend.data import SessionTranscript
from backend.domain.providers.types import Message, ToolUseEvent


def _wrap_summaries_for_prompt(summary_turns: list) -> str:
    """Render one or more compaction summaries as a single assistant-role prefix.

    Multiple summaries are concatenated with a separator so a long session
    with several compaction events reads as layered context, oldest first.
    Wrapped in <prior_conversation_summary> and followed by an anti-mimic
    note so the model treats it as reference, not a template to echo.
    """
    parts: list[str] = []
    for turn in summary_turns:
        text = (turn.text or "").strip()
        if text:
            parts.append(text)
    body = "\n\n---\n\n".join(parts)
    return (
        "<prior_conversation_summary>\n"
        + body
        + "\n</prior_conversation_summary>\n\n"
        "The block above is a compressed memo of earlier "
        "turns, provided for context only. I will answer the "
        "user's next message naturally in plain prose and "
        "will NOT reproduce the summary, its bullet-list "
        "formatting, or any 'tool X (completed): input=…' "
        "lines in my reply."
    )


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
            text=_wrap_summaries_for_prompt(summary_turns),
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
                },
                separators=(",", ":"),
            )
            messages.append(Message(
                role="tool_result",
                tool_use_id=tool_run.id,
                tool_content=content,
            ))
    return messages


def build_messages_from_raw(raw: Iterable[dict]) -> list[Message]:
    """Build the wire message sequence from a stateless request body.

    The Database helper chat carries its history in the browser and POSTs
    it back each turn, so the input is already in our own canonical
    shape (rather than a `SessionTranscript`). Each entry must have a
    `role` of `"user"`, `"assistant"`, or `"tool_result"`.

    Validation is intentionally light — the route enforces the shape via
    pydantic before we get here. This function only converts to the
    provider-facing dataclass.
    """
    out: list[Message] = []
    for entry in raw:
        role = entry.get("role")
        if role == "user":
            out.append(Message(role="user", text=entry.get("text") or ""))
        elif role == "assistant":
            tool_calls_raw = entry.get("tool_calls") or []
            tool_calls = [
                ToolUseEvent(
                    id=str(call["id"]),
                    name=str(call["name"]),
                    input=dict(call.get("input") or {}),
                )
                for call in tool_calls_raw
            ] or None
            out.append(Message(
                role="assistant",
                text=entry.get("text") or None,
                tool_calls=tool_calls,
            ))
        elif role == "tool_result":
            out.append(Message(
                role="tool_result",
                tool_use_id=entry.get("tool_use_id"),
                tool_content=entry.get("content") or "",
            ))
        # Unknown roles are dropped silently — pydantic rejects them at the
        # request boundary, so reaching this branch implies a programmer
        # error elsewhere.
    return out
