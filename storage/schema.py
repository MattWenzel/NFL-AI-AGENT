"""Schema initialization + column-add migrations + startup reconciliation.

`init_db` owns the baseline CREATE TABLE / CREATE INDEX statements plus
additive migrations (new columns on existing tables). Kept additive so a
pre-existing runtime DB upgrades in place without a rebuild.

`reconcile_interrupted_runs` runs on startup to mark any tool run or
assistant turn that was mid-flight when the previous process died as
'interrupted' — otherwise the transcript would still show them 'pending'
or 'running' forever.
"""

from __future__ import annotations

import logging
import sqlite3

from storage.records import utcnow

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    """Apply baseline schema + additive migrations. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    _ensure_column(conn, "turns", "input_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "turns", "output_tokens", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "sessions", "pinned_at", "TEXT")
    _ensure_column(conn, "sessions", "source_csv_id", "TEXT")
    _ensure_column(conn, "sessions", "user_id", "INTEGER REFERENCES users(id)")
    _ensure_column(conn, "exports", "user_id", "INTEGER REFERENCES users(id)")
    _ensure_column(conn, "tool_runs", "raw_input_text", "TEXT")
    # Multi-user additions. Both are additive (ADD COLUMN) so existing
    # rows get the DEFAULT / NULL. No table rebuild.
    _ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
    _ensure_column(conn, "users", "email_verified_at", "TEXT")
    # Indexes that depend on migrated columns go after _ensure_column so they
    # succeed on pre-existing DBs where the column is only just being added.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user_id, updated_at DESC)"
    )


def reconcile_interrupted_runs(conn: sqlite3.Connection) -> int:
    """Mark any tool run or assistant turn left mid-flight as 'interrupted'.

    Called on startup — the previous process may have died during tool
    execution. Without this sweep those rows stay 'pending'/'running'
    forever, which corrupts transcript views and wedges compaction
    selectors that skip completed-only turns.
    """
    now = utcnow()
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
        SET status = 'interrupted',
            error = COALESCE(error, 'Assistant turn interrupted by restart'),
            updated_at = ?
        WHERE role = 'assistant' AND status = 'running'
        """,
        (now,),
    )
    if count:
        logger.warning("Reconciled %d interrupted tool run(s) after startup", count)
    return count
