"""RuntimeStore facade: composes the per-domain mixins into one class.

All persistence concerns are served by a single class so callers have
one object to pass around. The domain-specific methods live in
`storage.users`, `storage.transcripts`, and `storage.exports`; this
module wires them together and owns the shared connection + per-session
asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from storage.exports import ExportsMixin
from storage.schema import init_db, reconcile_interrupted_runs
from storage.transcripts import TranscriptsMixin
from storage.users import UsersMixin


class RuntimeStore(UsersMixin, TranscriptsMixin, ExportsMixin):
    """SQLite-backed persistence for the refactored NFL agent runtime."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        with self._connect() as conn:
            init_db(conn)
        self.reconcile_interrupted_runs()

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def reconcile_interrupted_runs(self) -> int:
        """Mark mid-flight runs/turns as interrupted. Called on startup; also
        exposed as a method for tests that simulate restarts."""
        with self._connect() as conn:
            return reconcile_interrupted_runs(conn)
