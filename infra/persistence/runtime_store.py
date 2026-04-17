"""SQLite-backed runtime store for sessions, turns, assistant parts, and tool runs."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from infra.providers.base import Message, ToolUseEvent

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
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
    created_at: str = field(default_factory=_utcnow)


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


class RuntimeStore:
    """Persistence layer for the refactored NFL agent runtime."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        self._init_db()
        self.reconcile_interrupted_runs()

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    title TEXT,
                    context_window INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    compacted INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS assistant_parts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    name TEXT,
                    tool_run_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (turn_id) REFERENCES turns(id)
                );

                CREATE TABLE IF NOT EXISTS tool_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_text TEXT,
                    error_text TEXT,
                    hint TEXT,
                    duration_ms INTEGER,
                    compacted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (turn_id) REFERENCES turns(id)
                );

                CREATE TABLE IF NOT EXISTS compaction_summaries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    summary_turn_id TEXT NOT NULL,
                    source_turn_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (summary_turn_id) REFERENCES turns(id)
                );

                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    columns_json TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    source_session_id TEXT,
                    source_tool_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- users: one row per account. Future OAuth plan adds a
                -- user_identities(user_id, provider, provider_subject) table so
                -- one user can link multiple login methods; today's password-only
                -- users just have password_hash set.
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    email_verified_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_api_keys (
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, provider),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_turns_session_created
                    ON turns(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_parts_turn_order
                    ON assistant_parts(turn_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_tool_runs_turn_created
                    ON tool_runs(turn_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_exports_created
                    ON exports(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id);
                """
            )
            self._ensure_column(conn, "turns", "input_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "turns", "output_tokens", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "sessions", "pinned_at", "TEXT")
            self._ensure_column(conn, "sessions", "source_csv_id", "TEXT")
            self._ensure_column(conn, "sessions", "user_id", "INTEGER REFERENCES users(id)")
            self._ensure_column(conn, "exports", "user_id", "INTEGER REFERENCES users(id)")
            # Multi-user additions. Both are additive (ADD COLUMN) so existing
            # rows get the DEFAULT / NULL. No table rebuild.
            self._ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
            self._ensure_column(conn, "users", "email_verified_at", "TEXT")
            # Indexes that depend on migrated columns go after _ensure_column so they
            # succeed on pre-existing DBs where the column is only just being added.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user_id, updated_at DESC)"
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        cols = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def reconcile_interrupted_runs(self) -> int:
        now = _utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE tool_runs
                SET status = 'interrupted',
                    error_text = COALESCE(error_text, 'Tool execution interrupted by restart'),
                    updated_at = ?
                WHERE status IN ('pending', 'running')
                """,
                (now,),
            )
            count = cur.rowcount
            conn.execute(
                """
                UPDATE turns
                SET status = 'interrupted', error = COALESCE(error, 'Assistant turn interrupted by restart'), updated_at = ?
                WHERE role = 'assistant' AND status = 'running'
                """,
                (now,),
            )
        if count:
            logger.warning("Reconciled %d interrupted tool run(s) after startup", count)
        return count

    def get_or_create_session(
        self,
        session_id: str | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        context_window: int = 0,
        user_id: int | None = None,
    ) -> SessionRecord:
        existing = self.get_session(session_id, user_id=user_id) if session_id else None
        if existing:
            changed = False
            if provider and existing.provider != provider:
                existing.provider = provider
                changed = True
            if model and existing.model != model:
                existing.model = model
                changed = True
            if context_window and existing.context_window != context_window:
                existing.context_window = context_window
                changed = True
            if changed:
                self.update_session(existing)
            return existing

        now = _utcnow()
        session = SessionRecord(
            id=session_id or _new_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            context_window=context_window,
            user_id=user_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, created_at, updated_at, provider, model, title, context_window, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.created_at,
                    session.updated_at,
                    session.provider,
                    session.model,
                    session.title,
                    session.context_window,
                    session.user_id,
                ),
            )
        return session

    def update_session(self, session: SessionRecord) -> None:
        session.updated_at = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?, provider = ?, model = ?, title = ?,
                    context_window = ?, pinned_at = ?, source_csv_id = ?
                WHERE id = ?
                """,
                (
                    session.updated_at,
                    session.provider,
                    session.model,
                    session.title,
                    session.context_window,
                    session.pinned_at,
                    session.source_csv_id,
                    session.id,
                ),
            )

    def set_session_pinned(
        self, session_id: str, pinned: bool, *, user_id: int | None = None
    ) -> SessionRecord | None:
        """Pin or unpin a session. Pinning stamps pinned_at so callers can
        order most-recently-pinned first; unpinning clears it. Does not touch
        updated_at so pinning a stale conversation doesn't fake recency."""
        session = self.get_session(session_id, user_id=user_id)
        if session is None:
            return None
        session.pinned_at = _utcnow() if pinned else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET pinned_at = ? WHERE id = ?",
                (session.pinned_at, session_id),
            )
        return session

    def set_session_source_csv(
        self, session_id: str, export_id: str | None, *, user_id: int | None = None
    ) -> SessionRecord | None:
        session = self.get_session(session_id, user_id=user_id)
        if session is None:
            return None
        session.source_csv_id = export_id
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET source_csv_id = ? WHERE id = ?",
                (export_id, session_id),
            )
        return session

    def seed_summary(self, session_id: str, summary_text: str) -> TurnRecord:
        """Insert a synthetic summary turn without recording a compaction event.

        Used to seed a fresh session with assistant-visible context (e.g. the
        SQL that produced an opened CSV). The turn is stored with role='summary'
        so `build_model_messages` prefixes it with '[Compacted summary]' and
        the LLM treats it as established context — no separate branch in the
        runtime loop. We skip the compaction_summaries row because nothing is
        being compacted; transcript.summaries stays empty for real compaction
        events only.
        """
        return self.create_turn(session_id, "summary", text=summary_text, status="completed")

    def register_export(
        self,
        *,
        filename: str,
        title: str,
        sql: str,
        row_count: int,
        columns: list[str],
        file_size: int,
        source_session_id: str | None,
        source_tool_run_id: str | None,
    ) -> ExportRecord:
        now = _utcnow()
        record = ExportRecord(
            id=_new_id(),
            filename=filename,
            title=title,
            sql=sql,
            row_count=row_count,
            columns_json=json.dumps(columns),
            file_size=file_size,
            source_session_id=source_session_id,
            source_tool_run_id=source_tool_run_id,
            created_at=now,
            updated_at=now,
        )
        owning_user_id: int | None = None
        if source_session_id:
            sess = self.get_session(source_session_id)
            if sess is not None:
                owning_user_id = sess.user_id
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    id, filename, title, sql, row_count, columns_json, file_size,
                    source_session_id, source_tool_run_id, created_at, updated_at, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.filename,
                    record.title,
                    record.sql,
                    record.row_count,
                    record.columns_json,
                    record.file_size,
                    record.source_session_id,
                    record.source_tool_run_id,
                    record.created_at,
                    record.updated_at,
                    owning_user_id,
                ),
            )
        return record

    def list_exports(self, *, user_id: int | None = None) -> list[ExportRecord]:
        with self._connect() as conn:
            if user_id is not None:
                rows = conn.execute(
                    "SELECT * FROM exports WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM exports ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_export(r) for r in rows]

    def get_export(self, export_id: str, *, user_id: int | None = None) -> ExportRecord | None:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM exports WHERE id = ? AND user_id = ?",
                    (export_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
        return self._row_to_export(row) if row else None

    def get_export_by_filename(self, filename: str, *, user_id: int | None = None) -> ExportRecord | None:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM exports WHERE filename = ? AND user_id = ?",
                    (filename, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM exports WHERE filename = ?", (filename,)
                ).fetchone()
        return self._row_to_export(row) if row else None

    def update_export_title(
        self, export_id: str, title: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        now = _utcnow()
        with self._connect() as conn:
            if user_id is not None:
                cur = conn.execute(
                    "UPDATE exports SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (title, now, export_id, user_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE exports SET title = ?, updated_at = ? WHERE id = ?",
                    (title, now, export_id),
                )
            if cur.rowcount == 0:
                return None
        return self.get_export(export_id, user_id=user_id)

    def delete_export(
        self, export_id: str, *, user_id: int | None = None
    ) -> ExportRecord | None:
        """Remove the registry row. Caller is responsible for unlinking the
        on-disk file; returning the record so the caller knows the filename."""
        record = self.get_export(export_id, user_id=user_id)
        if record is None:
            return None
        with self._connect() as conn:
            if user_id is not None:
                conn.execute(
                    "DELETE FROM exports WHERE id = ? AND user_id = ?",
                    (export_id, user_id),
                )
            else:
                conn.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        return record

    def get_session(self, session_id: str | None, *, user_id: int | None = None) -> SessionRecord | None:
        if not session_id:
            return None
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def create_turn(self, session_id: str, role: str, text: str = "", status: str = "completed") -> TurnRecord:
        now = _utcnow()
        turn = TurnRecord(
            id=_new_id(),
            session_id=session_id,
            role=role,
            status=status,
            text=text,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (id, session_id, role, status, text, compacted, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (turn.id, turn.session_id, turn.role, turn.status, turn.text, turn.created_at, turn.updated_at),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return turn

    def update_turn(self, turn_id: str, **changes) -> TurnRecord:
        if not changes:
            return self.get_turn(turn_id)
        fields = []
        values = []
        for key, value in changes.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.extend([_utcnow(), turn_id])
        sql = f"UPDATE turns SET {', '.join(fields)}, updated_at = ? WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, values)
        return self.get_turn(turn_id)

    def append_turn_text(self, turn_id: str, text: str) -> TurnRecord:
        turn = self.get_turn(turn_id)
        if turn is None:
            raise KeyError(f"Unknown turn {turn_id}")
        return self.update_turn(turn_id, text=turn.text + text)

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        return self._row_to_turn(row) if row else None

    def add_part(
        self,
        session_id: str,
        turn_id: str,
        kind: str,
        content: str,
        *,
        name: str | None = None,
        tool_run_id: str | None = None,
    ) -> AssistantPartRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(order_index), -1) + 1 FROM assistant_parts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            order_index = int(row[0]) if row else 0
            part = AssistantPartRecord(
                id=_new_id(),
                session_id=session_id,
                turn_id=turn_id,
                kind=kind,
                order_index=order_index,
                content=content,
                name=name,
                tool_run_id=tool_run_id,
            )
            conn.execute(
                """
                INSERT INTO assistant_parts (id, session_id, turn_id, kind, order_index, content, name, tool_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part.id,
                    part.session_id,
                    part.turn_id,
                    part.kind,
                    part.order_index,
                    part.content,
                    part.name,
                    part.tool_run_id,
                    part.created_at,
                ),
            )
        return part

    def create_tool_run(self, session_id: str, turn_id: str, tool_name: str, input_data: dict, status: str = "pending") -> ToolRunRecord:
        now = _utcnow()
        tool_run = ToolRunRecord(
            id=_new_id(),
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            input_json=json.dumps(input_data, sort_keys=True),
            status=status,
            result_text=None,
            error_text=None,
            hint=None,
            duration_ms=None,
            compacted=False,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_runs (
                    id, session_id, turn_id, tool_name, input_json, status,
                    result_text, error_text, hint, duration_ms, compacted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 0, ?, ?)
                """,
                (
                    tool_run.id,
                    tool_run.session_id,
                    tool_run.turn_id,
                    tool_run.tool_name,
                    tool_run.input_json,
                    tool_run.status,
                    tool_run.created_at,
                    tool_run.updated_at,
                ),
            )
        return tool_run

    def update_tool_run(self, tool_run_id: str, **changes) -> ToolRunRecord:
        if not changes:
            return self.get_tool_run(tool_run_id)
        fields = []
        values = []
        for key, value in changes.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.extend([_utcnow(), tool_run_id])
        sql = f"UPDATE tool_runs SET {', '.join(fields)}, updated_at = ? WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, values)
        return self.get_tool_run(tool_run_id)

    def get_tool_run(self, tool_run_id: str) -> ToolRunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tool_runs WHERE id = ?", (tool_run_id,)).fetchone()
        return self._row_to_tool_run(row) if row else None

    def get_recent_tool_runs(self, session_id: str, limit: int = 3) -> list[ToolRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tool_runs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [self._row_to_tool_run(r) for r in rows]

    def record_compaction(self, session_id: str, summary_text: str, source_turn_ids: list[str]) -> CompactionSummaryRecord:
        summary_turn = self.create_turn(session_id, "summary", text=summary_text, status="completed")
        now = _utcnow()
        summary = CompactionSummaryRecord(
            id=_new_id(),
            session_id=session_id,
            summary_turn_id=summary_turn.id,
            source_turn_ids=source_turn_ids,
            created_at=now,
        )
        encoded = json.dumps(source_turn_ids)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO compaction_summaries (id, session_id, summary_turn_id, source_turn_ids, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (summary.id, session_id, summary.summary_turn_id, encoded, summary.created_at),
            )
            if source_turn_ids:
                placeholders = ", ".join("?" for _ in source_turn_ids)
                conn.execute(
                    f"UPDATE turns SET compacted = 1, updated_at = ? WHERE id IN ({placeholders})",
                    (now, *source_turn_ids),
                )
                conn.execute(
                    f"""
                    UPDATE tool_runs
                    SET compacted = 1, updated_at = ?
                    WHERE turn_id IN ({placeholders}) AND status = 'completed'
                    """,
                    (now, *source_turn_ids),
                )
        return summary

    def get_transcript(self, session_id: str) -> SessionTranscript:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session {session_id}")
        with self._connect() as conn:
            turn_rows = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            part_rows = conn.execute(
                """
                SELECT * FROM assistant_parts
                WHERE session_id = ?
                ORDER BY turn_id, order_index, created_at
                """,
                (session_id,),
            ).fetchall()
            tool_rows = conn.execute(
                "SELECT * FROM tool_runs WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            summary_rows = conn.execute(
                "SELECT * FROM compaction_summaries WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        turns = [self._row_to_turn(r) for r in turn_rows]
        parts_by_turn: dict[str, list[AssistantPartRecord]] = {}
        for row in part_rows:
            part = self._row_to_part(row)
            parts_by_turn.setdefault(part.turn_id, []).append(part)
        tool_runs_by_turn: dict[str, list[ToolRunRecord]] = {}
        for row in tool_rows:
            tool_run = self._row_to_tool_run(row)
            tool_runs_by_turn.setdefault(tool_run.turn_id, []).append(tool_run)
        summaries = [self._row_to_summary(r) for r in summary_rows]
        return SessionTranscript(
            session=session,
            turns=turns,
            parts_by_turn=parts_by_turn,
            tool_runs_by_turn=tool_runs_by_turn,
            summaries=summaries,
        )

    def build_model_messages(self, session_id: str) -> list[Message]:
        transcript = self.get_transcript(session_id)
        messages: list[Message] = []
        for turn in transcript.turns:
            if turn.compacted:
                continue
            if turn.role == "user":
                messages.append(Message(role="user", text=turn.text))
                continue
            if turn.role in {"assistant", "summary"}:
                parts = transcript.parts_by_turn.get(turn.id, [])
                text = turn.text
                if not text:
                    text = "".join(part.content for part in parts if part.kind == "text")
                # Anthropic rejects messages whose final assistant content
                # ends with trailing whitespace ("messages: final assistant
                # content cannot end with trailing whitespace", 400). Models
                # stream text that ends with \n frequently; strip before
                # replay so a single past turn doesn't wedge every future
                # call on the session.
                text = (text or "").rstrip()
                tool_calls = []
                for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
                    tool_calls.append(
                        ToolUseEvent(
                            id=tool_run.id,
                            name=tool_run.tool_name,
                            input=safe_load_tool_input(tool_run.input_json, tool_run_id=tool_run.id),
                        )
                    )
                if turn.role == "summary":
                    # Wrap the summary in an XML tag and end with a self-note
                    # so the model treats it as a reference, not as its own
                    # past content to mimic. Without this framing the model
                    # sees a bullet-list "assistant" message and starts
                    # echoing that exact format ("- user: …", "- tool
                    # execute_sql (completed): input=…") in its next reply.
                    summary_text = (
                        "<prior_conversation_summary>\n"
                        + text
                        + "\n</prior_conversation_summary>\n\n"
                        "The block above is a compressed memo of earlier "
                        "turns, provided for context only. I will answer the "
                        "user's next message naturally in plain prose and "
                        "will NOT reproduce the summary, its bullet-list "
                        "formatting, or any 'tool X (completed): input=…' "
                        "lines in my reply."
                    )
                    messages.append(Message(role="assistant", text=summary_text))
                elif text or tool_calls:
                    messages.append(
                        Message(
                            role="assistant",
                            text=text or None,
                            tool_calls=tool_calls or None,
                        )
                    )
                for tool_run in transcript.tool_runs_by_turn.get(turn.id, []):
                    if tool_run.compacted:
                        continue
                    content = tool_run.result_text or json.dumps(
                        {
                            "status": tool_run.status,
                            "error": tool_run.error_text or "Tool run incomplete",
                            "hint": tool_run.hint,
                        },
                        separators=(",", ":"),
                    )
                    messages.append(
                        Message(
                            role="tool_result",
                            tool_use_id=tool_run.id,
                            tool_content=content,
                        )
                    )
        return messages

    def list_sessions(self, *, user_id: int | None = None) -> list[dict]:
        with self._connect() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """
                    SELECT s.id,
                           s.updated_at,
                           s.pinned_at,
                           s.source_csv_id,
                           COALESCE(s.title, (
                               SELECT SUBSTR(text, 1, 60)
                               FROM turns t
                               WHERE t.session_id = s.id AND t.role = 'user'
                               ORDER BY t.created_at
                               LIMIT 1
                           ), 'New conversation') AS title,
                           s.provider,
                           s.model,
                           (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count
                    FROM sessions s
                    WHERE s.user_id = ?
                    ORDER BY s.pinned_at DESC, s.updated_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT s.id,
                           s.updated_at,
                           s.pinned_at,
                           s.source_csv_id,
                           COALESCE(s.title, (
                               SELECT SUBSTR(text, 1, 60)
                               FROM turns t
                               WHERE t.session_id = s.id AND t.role = 'user'
                               ORDER BY t.created_at
                               LIMIT 1
                           ), 'New conversation') AS title,
                           s.provider,
                           s.model,
                           (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count
                    FROM sessions s
                    ORDER BY s.pinned_at DESC, s.updated_at DESC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str, *, user_id: int | None = None) -> bool:
        with self._connect() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM assistant_parts WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM tool_runs WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM compaction_summaries WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._locks.pop(session_id, None)
        return True

    # ---------------- users ----------------

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        email_verified_at: str | None = None,
    ) -> UserRecord:
        now = _utcnow()
        normalized = email.strip().lower()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (email, password_hash, created_at, updated_at, role, email_verified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, password_hash, now, now, role, email_verified_at),
            )
            user_id = int(cur.lastrowid)
        return UserRecord(
            id=user_id,
            email=normalized,
            password_hash=password_hash,
            created_at=now,
            updated_at=now,
            role=role,
            email_verified_at=email_verified_at,
        )

    def get_user_by_email(self, email: str) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> UserRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def update_user_password(self, user_id: int, password_hash: str) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, now, user_id),
            )

    def delete_user(self, user_id: int) -> list[str]:
        """Full cascade delete of a user and all owned data.

        Returns the list of CSV filenames that were registered to this user so
        the caller can unlink them from disk — the DB row is gone by then.

        Order matters: sessions first (each via `delete_session` to cascade
        turns / parts / tool_runs / compaction_summaries), then exports, then
        the user row. auth_sessions and user_api_keys cascade automatically
        via the FK ON DELETE CASCADE declared in _init_db.
        """
        session_ids = [s["id"] for s in self.list_sessions(user_id=user_id)]
        for sid in session_ids:
            self.delete_session(sid, user_id=user_id)
        filenames = [e.filename for e in self.list_exports(user_id=user_id)]
        with self._connect() as conn:
            conn.execute("DELETE FROM exports WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return filenames

    def count_orphan_rows(self) -> dict[str, int]:
        """Rows with NULL user_id in user-scoped tables.

        Expected to be {0, 0} after first registration (backfill_orphan_ownership
        runs once). A non-zero result in multi-user mode means data is invisible
        to the scoped queries — surfaced as a startup warning.
        """
        with self._connect() as conn:
            sessions_null = int(conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id IS NULL"
            ).fetchone()[0])
            exports_null = int(conn.execute(
                "SELECT COUNT(*) FROM exports WHERE user_id IS NULL"
            ).fetchone()[0])
        return {"sessions": sessions_null, "exports": exports_null}

    def ensure_admin_exists(self) -> int | None:
        """Promote the oldest user to admin if no admin exists.

        Safety net for DBs that predate the `role` column (pre-multi-user
        registrations default to 'user' via ALTER TABLE). Returns the promoted
        user's id, or None if the invariant already held / there are no users.
        """
        with self._connect() as conn:
            has_admin = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            if has_admin is not None:
                return None
            row = conn.execute(
                "SELECT id FROM users ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            uid = int(row["id"])
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (uid,))
            return uid

    def backfill_orphan_ownership(self, user_id: int) -> tuple[int, int]:
        """Assign the given user_id to any sessions/exports that have none.

        Called once after first registration in single-user mode so pre-existing
        conversations/exports become owned by that user.
        """
        with self._connect() as conn:
            s = conn.execute(
                "UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            e = conn.execute(
                "UPDATE exports SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            return (s.rowcount, e.rowcount)

    # ---------------- user_api_keys ----------------

    def upsert_api_key(self, *, user_id: int, provider: str, encrypted_key: str) -> UserApiKeyRecord:
        now = _utcnow()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO user_api_keys (user_id, provider, encrypted_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    encrypted_key = excluded.encrypted_key,
                    updated_at = excluded.updated_at
                """,
                (user_id, provider, encrypted_key, created_at, now),
            )
        return UserApiKeyRecord(
            user_id=user_id,
            provider=provider,
            encrypted_key=encrypted_key,
            created_at=created_at,
            updated_at=now,
        )

    def delete_api_key(self, *, user_id: int, provider: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )
            return cur.rowcount > 0

    def get_api_key(self, *, user_id: int, provider: str) -> UserApiKeyRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return self._row_to_api_key(row) if row else None

    def list_api_keys(self, user_id: int) -> list[UserApiKeyRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_api_keys WHERE user_id = ? ORDER BY provider", (user_id,)
            ).fetchall()
        return [self._row_to_api_key(r) for r in rows]

    def user_has_api_key(self, *, user_id: int, provider: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            ).fetchone()
        return row is not None

    # ---------------- auth_sessions ----------------

    def create_auth_session(
        self, *, token: str, user_id: int, expires_at: str
    ) -> AuthSessionRecord:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (token, user_id, created_at, expires_at, last_used_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, user_id, now, expires_at, now),
            )
        return AuthSessionRecord(
            token=token,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            last_used_at=now,
        )

    def get_auth_session(self, token: str) -> AuthSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_sessions WHERE token = ?", (token,)
            ).fetchone()
        return self._row_to_auth_session(row) if row else None

    def touch_auth_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET last_used_at = ? WHERE token = ?",
                (_utcnow(), token),
            )

    def delete_auth_session(self, token: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            return cur.rowcount > 0

    def invalidate_other_auth_sessions(self, user_id: int, keep_token: str) -> int:
        """Revoke every auth session for this user except the one in use.

        Called after a password change so stolen tokens can't outlive the
        rotation. Returns the number of tokens killed.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND token != ?",
                (user_id, keep_token),
            )
            return cur.rowcount

    def purge_expired_auth_sessions(self) -> int:
        now = _utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (now,)
            )
            return cur.rowcount

    # ---------------- row mappers ----------------

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> UserRecord:
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

    @staticmethod
    def _row_to_api_key(row: sqlite3.Row) -> UserApiKeyRecord:
        return UserApiKeyRecord(
            user_id=int(row["user_id"]),
            provider=row["provider"],
            encrypted_key=row["encrypted_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_auth_session(row: sqlite3.Row) -> AuthSessionRecord:
        return AuthSessionRecord(
            token=row["token"],
            user_id=int(row["user_id"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
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

    @staticmethod
    def _row_to_export(row: sqlite3.Row) -> ExportRecord:
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

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> TurnRecord:
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

    @staticmethod
    def _row_to_part(row: sqlite3.Row) -> AssistantPartRecord:
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

    @staticmethod
    def _row_to_tool_run(row: sqlite3.Row) -> ToolRunRecord:
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
        )

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> CompactionSummaryRecord:
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
