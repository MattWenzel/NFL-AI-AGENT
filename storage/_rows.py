"""sqlite3.Row → dataclass mappers, shared by every store mixin."""

from __future__ import annotations

import json
import logging
import sqlite3

from storage.records import (
    AssistantPartRecord,
    AuthSessionRecord,
    CompactionSummaryRecord,
    ExportRecord,
    SessionRecord,
    ToolRunRecord,
    TurnRecord,
    UserApiKeyRecord,
    UserRecord,
)

logger = logging.getLogger(__name__)


def row_to_user(row: sqlite3.Row) -> UserRecord:
    keys = row.keys()
    return UserRecord(
        id=int(row["id"]),
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        role=row["role"] if "role" in keys and row["role"] is not None else "user",
        email_verified_at=row["email_verified_at"] if "email_verified_at" in keys else None,
    )


def row_to_api_key(row: sqlite3.Row) -> UserApiKeyRecord:
    return UserApiKeyRecord(
        user_id=int(row["user_id"]),
        provider=row["provider"],
        encrypted_key=row["encrypted_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_auth_session(row: sqlite3.Row) -> AuthSessionRecord:
    return AuthSessionRecord(
        token=row["token"],
        user_id=int(row["user_id"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
    )


def row_to_session(row: sqlite3.Row) -> SessionRecord:
    keys = row.keys()
    return SessionRecord(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        provider=row["provider"],
        model=row["model"],
        title=row["title"],
        context_window=row["context_window"],
        pinned_at=row["pinned_at"] if "pinned_at" in keys else None,
        source_csv_id=row["source_csv_id"] if "source_csv_id" in keys else None,
        user_id=row["user_id"] if "user_id" in keys else None,
    )


def row_to_export(row: sqlite3.Row) -> ExportRecord:
    return ExportRecord(
        id=row["id"],
        filename=row["filename"],
        title=row["title"],
        sql=row["sql"],
        row_count=row["row_count"],
        columns_json=row["columns_json"],
        file_size=row["file_size"],
        source_session_id=row["source_session_id"],
        source_tool_run_id=row["source_tool_run_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_turn(row: sqlite3.Row) -> TurnRecord:
    return TurnRecord(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        status=row["status"],
        text=row["text"] or "",
        compacted=bool(row["compacted"]),
        error=row["error"],
        input_tokens=row["input_tokens"] if "input_tokens" in row.keys() else 0,
        output_tokens=row["output_tokens"] if "output_tokens" in row.keys() else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_part(row: sqlite3.Row) -> AssistantPartRecord:
    return AssistantPartRecord(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        kind=row["kind"],
        order_index=row["order_index"],
        content=row["content"] or "",
        name=row["name"],
        tool_run_id=row["tool_run_id"],
        created_at=row["created_at"],
    )


def row_to_tool_run(row: sqlite3.Row) -> ToolRunRecord:
    keys = row.keys()
    return ToolRunRecord(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        tool_name=row["tool_name"],
        input_json=row["input_json"],
        status=row["status"],
        result_text=row["result_text"],
        error_text=row["error_text"],
        hint=row["hint"],
        duration_ms=row["duration_ms"],
        compacted=bool(row["compacted"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        raw_input_text=row["raw_input_text"] if "raw_input_text" in keys else None,
    )


def row_to_summary(row: sqlite3.Row) -> CompactionSummaryRecord:
    # Fall back to [] on malformed source_turn_ids so a single corrupt
    # compaction row doesn't wedge get_transcript() (and therefore the
    # whole session — chat loop, UI sidebar, /history).
    raw = row["source_turn_ids"]
    try:
        source_turn_ids = json.loads(raw) if raw else []
    except json.JSONDecodeError as exc:
        logger.warning(
            "Malformed compaction_summary.source_turn_ids (summary_id=%s, session_id=%s): %s",
            row["id"], row["session_id"], exc,
        )
        source_turn_ids = []
    if not isinstance(source_turn_ids, list):
        logger.warning(
            "compaction_summary.source_turn_ids is not a list (summary_id=%s, type=%s)",
            row["id"], type(source_turn_ids).__name__,
        )
        source_turn_ids = []
    return CompactionSummaryRecord(
        id=row["id"],
        session_id=row["session_id"],
        summary_turn_id=row["summary_turn_id"],
        source_turn_ids=source_turn_ids,
        created_at=row["created_at"],
    )
