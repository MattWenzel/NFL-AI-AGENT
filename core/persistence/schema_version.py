"""Minimal SQLite schema-version migration system.

Replaces Alembic for this app, since we only use a narrow slice of its
features (ordered apply on startup + a version tracker). Uses SQLite's
built-in `PRAGMA user_version` as the tracker; migrations are plain
Python callables that take a sync Connection.

A one-time seam reads `alembic_version` when present (existing
Alembic-managed DBs) and seeds `user_version` to match so no migration
re-runs. The seam is harmless on fresh DBs (no `alembic_version` table)
and stays in place forever — it's a no-op on DBs where `user_version`
is already set.

Every migration is idempotent (CREATE TABLE IF NOT EXISTS, guarded
column renames) so a fresh DB that already has the post-migration shape
via `SQLModel.metadata.create_all` runs every migration as a no-op.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from core.persistence.models import SQLModel

logger = logging.getLogger(__name__)


def _migration_0001_initial_schema(conn: Connection) -> None:
    """Create every runtime table from the current SQLModel metadata.

    `metadata.create_all` emits CREATE TABLE IF NOT EXISTS for each table,
    so pre-existing DBs bind as a no-op.
    """
    SQLModel.metadata.create_all(conn)


_TOOLRUN_LEGACY_TO_NEW = {
    "input_json": "input",
    "result_text": "result",
    "error_text": "error",
}


def _migration_0002_rename_toolrun_columns(conn: Connection) -> None:
    """Rename tool_runs columns to match Python attribute names.

    Only alters anything on legacy DBs that predate the rename; fresh
    DBs already have the new names from the 0001 baseline. Uses SQLite's
    native RENAME COLUMN (available since 3.25; Python 3.12 ships 3.40+).
    """
    existing = {
        row[1] for row in conn.execute(text("PRAGMA table_info(tool_runs)"))
    }
    for old, new in _TOOLRUN_LEGACY_TO_NEW.items():
        if old in existing:
            conn.execute(text(f"ALTER TABLE tool_runs RENAME COLUMN {old} TO {new}"))


def _migration_0003_security_hardening(conn: Connection) -> None:
    """Email verification + per-email login lockout + audit log tables.

    Mirrors the original 0003 Alembic migration byte-for-byte at the SQL
    level (same columns, same defaults, same FK actions, same indexes).
    """
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS email_verifications (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL DEFAULT 'signup',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        )
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_email_verifications_user "
        "ON email_verifications(user_id)"
    ))
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS login_failures (
            email TEXT PRIMARY KEY,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_failure_at TEXT NOT NULL,
            locked_until TEXT
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS security_events (
            id TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_security_events_user_created "
        "ON security_events(user_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_security_events_created "
        "ON security_events(created_at DESC)"
    ))


def _migration_0004_oauth_identities(conn: Connection) -> None:
    """OAuth + password identity registry."""
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS user_identities (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_subject TEXT NOT NULL,
            email TEXT,
            created_at TEXT NOT NULL,
            CONSTRAINT uq_user_identities_provider_subject
                UNIQUE (provider, provider_subject)
        )
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_user_identities_user "
        "ON user_identities(user_id)"
    ))


# Ordered migration list. `user_version` after a full apply == len(MIGRATIONS).
# Append-only — never reorder or delete entries or the version tracker drifts.
MIGRATIONS: list[Callable[[Connection], None]] = [
    _migration_0001_initial_schema,
    _migration_0002_rename_toolrun_columns,
    _migration_0003_security_hardening,
    _migration_0004_oauth_identities,
]


# One-time seam: map the legacy Alembic version_num strings to our integer
# indexes. Kept here (not inlined) so future additions to MIGRATIONS don't
# force a rethink of the seam — the seam only maps pre-cutover versions.
_ALEMBIC_VERSION_MAP = {
    "0001_initial_schema": 1,
    "0002_rename_toolrun_columns": 2,
    "0003_security_hardening": 3,
    "0004_oauth_identities": 4,
}


def _resolve_current_version(conn: Connection) -> int:
    """Determine the starting `user_version` for this DB.

    Three cases:
      - `user_version` already set (nothing to do, return it)
      - Existing Alembic-managed DB (has `alembic_version` table — read it,
        map the version_num string via `_ALEMBIC_VERSION_MAP`, return that)
      - Fresh DB (no `alembic_version` table, return 0)
    """
    current = conn.execute(text("PRAGMA user_version")).scalar()
    if current and current > 0:
        return int(current)
    has_alembic = conn.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='alembic_version'"
    )).first() is not None
    if not has_alembic:
        return 0
    row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    if row is None:
        return 0
    mapped = _ALEMBIC_VERSION_MAP.get(row[0])
    if mapped is None:
        logger.warning(
            "alembic_version=%r not in migration map — treating as unmigrated",
            row[0],
        )
        return 0
    return mapped


def apply_migrations(engine: Engine) -> None:
    """Apply any pending schema migrations on startup.

    Single transaction across all pending migrations — a mid-migration
    failure rolls back the entire batch, leaving `user_version` unchanged
    so the next boot retries from the same starting point.

    On a DB that was pre-cutover stamped via Alembic, the seam resolves
    a positive starting version from `alembic_version`; we persist it to
    `user_version` even when no migrations run so the seam is a one-shot
    (future startups short-circuit on the `PRAGMA user_version` read).
    """
    with engine.begin() as conn:
        current = _resolve_current_version(conn)
        target = len(MIGRATIONS)
        # user_version can't take bind params — both values are small ints
        # we control, so inline interpolation is safe.
        if current >= target:
            conn.execute(text(f"PRAGMA user_version = {current}"))
            return
        logger.info("applying schema migrations %d → %d", current, target)
        for idx, migrate in enumerate(MIGRATIONS, start=1):
            if idx <= current:
                continue
            migrate(conn)
            conn.execute(text(f"PRAGMA user_version = {idx}"))
