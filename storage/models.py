"""SQLModel table classes + shared helpers.

These classes are the ORM-mapped domain types. Column names, types, defaults,
indexes, and foreign keys mirror the existing `storage/schema.py` post-migration
shape exactly, so pre-existing `runtime.sqlite3` files bind cleanly to the new
models without schema changes.

Legacy aliases (`SessionRecord` etc.) are preserved as the class names to keep
the blast radius of the ORM migration small — external importers (agent/,
server/, auth/) keep working unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Column, ForeignKey, Index, Integer, desc
from sqlmodel import Field, SQLModel

from storage.types import TolerantJSONList, ToolInputJSON

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


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


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("idx_sessions_user_updated", "user_id", desc("updated_at")),
    )

    id: str = Field(primary_key=True)
    created_at: str
    updated_at: str
    provider: str | None = None
    model: str | None = None
    title: str | None = None
    context_window: int = 0
    pinned_at: str | None = None
    source_csv_id: str | None = None
    user_id: int | None = Field(default=None, foreign_key="users.id")


class TurnRecord(SQLModel, table=True):
    __tablename__ = "turns"
    __table_args__ = (
        Index("idx_turns_session_created", "session_id", "created_at"),
    )

    id: str = Field(primary_key=True)
    # session_id is redundant on nested transcript responses — the outer
    # response already identifies the session. Hide from wire, keep on
    # the ORM model so scoped queries can filter.
    session_id: str = Field(foreign_key="sessions.id", exclude=True)
    role: str
    status: str
    text: str = ""
    compacted: bool = False
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: str
    updated_at: str


class AssistantPartRecord(SQLModel, table=True):
    __tablename__ = "assistant_parts"
    __table_args__ = (
        Index("idx_parts_turn_order", "turn_id", "order_index"),
    )

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", exclude=True)
    turn_id: str = Field(foreign_key="turns.id")
    kind: str
    order_index: int
    content: str = ""
    name: str | None = None
    tool_run_id: str | None = None
    created_at: str = Field(default_factory=utcnow)


class ToolRunRecord(SQLModel, table=True):
    __tablename__ = "tool_runs"
    __table_args__ = (
        Index("idx_tool_runs_turn_created", "turn_id", "created_at"),
    )

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", exclude=True)
    turn_id: str = Field(foreign_key="turns.id")
    tool_name: str
    # Parsed dict on the Python side and in the wire shape; canonical JSON
    # on disk. ToolInputJSON's tolerant decoder returns `{}` on malformed
    # rows so one bad row doesn't wedge the session.
    input: dict = Field(
        default_factory=dict,
        sa_column=Column(ToolInputJSON, nullable=False),
    )
    status: str
    result: str | None = None
    error: str | None = None
    hint: str | None = None
    duration_ms: int | None = None
    compacted: bool = False
    created_at: str
    updated_at: str


class CompactionSummaryRecord(SQLModel, table=True):
    __tablename__ = "compaction_summaries"

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", exclude=True)
    summary_turn_id: str = Field(foreign_key="turns.id")
    source_turn_ids: list[str] = Field(
        default_factory=list,
        sa_column=Column(TolerantJSONList, nullable=False),
    )
    created_at: str


class ExportRecord(SQLModel, table=True):
    __tablename__ = "exports"
    __table_args__ = (
        Index("idx_exports_created", desc("created_at")),
    )

    id: str = Field(primary_key=True)
    filename: str = Field(unique=True)
    title: str
    sql: str
    row_count: int
    columns: list[str] = Field(
        default_factory=list,
        sa_column=Column("columns_json", TolerantJSONList, nullable=False),
    )
    file_size: int
    source_session_id: str | None = None
    source_tool_run_id: str | None = None
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_at: str
    updated_at: str


class UserRecord(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    password_hash: str
    created_at: str
    updated_at: str
    role: str = "user"
    email_verified_at: str | None = None


class UserApiKeyRecord(SQLModel, table=True):
    __tablename__ = "user_api_keys"

    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    provider: str = Field(primary_key=True)
    encrypted_key: str
    created_at: str
    updated_at: str


class AuthSessionRecord(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("idx_auth_sessions_user", "user_id"),
    )

    token: str = Field(primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    created_at: str
    expires_at: str
    last_used_at: str


@dataclass(frozen=True)
class SessionListEntry:
    """Typed session-list projection returned by storage read helpers."""

    id: str
    turn_count: int
    title: str
    provider: str | None
    model: str | None
    updated_at: str | None
    pinned_at: str | None
    source_csv_id: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SessionListEntry":
        return cls(
            id=str(row["id"]),
            turn_count=int(row["turn_count"]),
            title=str(row["title"]),
            provider=row.get("provider"),
            model=row.get("model"),
            updated_at=row.get("updated_at"),
            pinned_at=row.get("pinned_at"),
            source_csv_id=row.get("source_csv_id"),
        )


@dataclass
class SessionTranscript:
    """Composite container returned by `get_transcript` — not a table."""

    session: SessionRecord
    turns: list[TurnRecord]
    parts_by_turn: dict[str, list[AssistantPartRecord]]
    tool_runs_by_turn: dict[str, list[ToolRunRecord]]
    summaries: list[CompactionSummaryRecord]
