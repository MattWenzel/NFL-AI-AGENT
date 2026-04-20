"""Dataclass records + free-standing helpers shared across store modules.

These are the persistence-layer value types. They're plain dataclasses so
callers (chat runtime, HTTP routes, tests) can construct and pass them
without touching SQLite.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def safe_load_tool_input(raw: str | None, *, tool_run_id: str | None = None) -> dict:
    """Parse a persisted tool_run.input_json, returning {} on malformed content.

    A single corrupted row otherwise wedges compaction, message build, and tool
    execution for the whole session. Log loudly and keep going.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Malformed tool_run.input_json (tool_run_id=%s): %s",
            tool_run_id, exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "tool_run.input_json is not a JSON object (tool_run_id=%s, type=%s)",
            tool_run_id, type(parsed).__name__,
        )
        return {}
    return parsed


def wrap_summaries_for_prompt(summary_turns: list["TurnRecord"]) -> str:
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


@dataclass
class SessionRecord:
    id: str
    created_at: str
    updated_at: str
    provider: str | None = None
    model: str | None = None
    title: str | None = None
    context_window: int = 0
    pinned_at: str | None = None
    source_csv_id: str | None = None
    user_id: int | None = None


@dataclass
class UserRecord:
    id: int
    email: str
    password_hash: str
    created_at: str
    updated_at: str
    role: str = "user"
    email_verified_at: str | None = None


@dataclass
class UserApiKeyRecord:
    user_id: int
    provider: str
    encrypted_key: str
    created_at: str
    updated_at: str


@dataclass
class AuthSessionRecord:
    token: str
    user_id: int
    created_at: str
    expires_at: str
    last_used_at: str


@dataclass
class ExportRecord:
    id: str
    filename: str
    title: str
    sql: str
    row_count: int
    columns_json: str
    file_size: int
    source_session_id: str | None
    source_tool_run_id: str | None
    created_at: str
    updated_at: str


@dataclass
class TurnRecord:
    id: str
    session_id: str
    role: str
    status: str
    text: str
    created_at: str
    updated_at: str
    compacted: bool = False
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AssistantPartRecord:
    id: str
    session_id: str
    turn_id: str
    kind: str
    order_index: int
    content: str
    name: str | None = None
    tool_run_id: str | None = None
    created_at: str = field(default_factory=utcnow)


@dataclass
class ToolRunRecord:
    id: str
    session_id: str
    turn_id: str
    tool_name: str
    input_json: str
    status: str
    result_text: str | None
    error_text: str | None
    hint: str | None
    duration_ms: int | None
    compacted: bool
    created_at: str
    updated_at: str
    # Captured when the model's streamed JSON arguments fail to parse
    # — input_json gets the {} fallback so the loop keeps moving, and
    # this preserves the original bytes for post-hoc debugging.
    raw_input_text: str | None = None


@dataclass
class CompactionSummaryRecord:
    id: str
    session_id: str
    summary_turn_id: str
    source_turn_ids: list[str]
    created_at: str


@dataclass
class SessionTranscript:
    session: SessionRecord
    turns: list[TurnRecord]
    parts_by_turn: dict[str, list[AssistantPartRecord]]
    tool_runs_by_turn: dict[str, list[ToolRunRecord]]
    summaries: list[CompactionSummaryRecord]
