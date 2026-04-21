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

    async def get_or_create_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_or_create_session, *args, **kwargs)

    async def get_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_session, *args, **kwargs)

    async def list_sessions_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.list_sessions, *args, **kwargs)

    async def get_session_list_row_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_session_list_row, *args, **kwargs)

    async def delete_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_session, *args, **kwargs)

    async def set_session_pinned_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.set_session_pinned, *args, **kwargs)

    async def set_session_source_csv_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.set_session_source_csv, *args, **kwargs)

    async def seed_summary_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.seed_summary, *args, **kwargs)

    async def create_turn_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.create_turn, *args, **kwargs)

    async def update_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.update_session, *args, **kwargs)

    async def append_assistant_text_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.append_assistant_text, *args, **kwargs)

    async def list_exports_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.list_exports, *args, **kwargs)

    async def get_export_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_export, *args, **kwargs)

    async def get_export_by_filename_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_export_by_filename, *args, **kwargs)

    async def update_export_title_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.update_export_title, *args, **kwargs)

    async def delete_export_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_export, *args, **kwargs)

    async def create_tool_run_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.create_tool_run, *args, **kwargs)

    async def add_part_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.add_part, *args, **kwargs)

    async def update_turn_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.update_turn, *args, **kwargs)

    async def get_turn_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_turn, *args, **kwargs)

    async def get_tool_run_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_tool_run, *args, **kwargs)

    async def get_transcript_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_transcript, *args, **kwargs)

    async def update_tool_run_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.update_tool_run, *args, **kwargs)

    async def record_compaction_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.record_compaction, *args, **kwargs)

    async def count_users_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.count_users, *args, **kwargs)

    async def create_user_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.create_user, *args, **kwargs)

    async def get_user_by_email_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_user_by_email, *args, **kwargs)

    async def get_user_by_id_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_user_by_id, *args, **kwargs)

    async def update_user_password_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.update_user_password, *args, **kwargs)

    async def delete_user_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_user, *args, **kwargs)

    async def backfill_orphan_ownership_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.backfill_orphan_ownership, *args, **kwargs)

    async def upsert_api_key_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.upsert_api_key, *args, **kwargs)

    async def delete_api_key_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_api_key, *args, **kwargs)

    async def get_api_key_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_api_key, *args, **kwargs)

    async def list_api_keys_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.list_api_keys, *args, **kwargs)

    async def user_has_api_key_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.user_has_api_key, *args, **kwargs)

    async def create_auth_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.create_auth_session, *args, **kwargs)

    async def get_auth_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.get_auth_session, *args, **kwargs)

    async def touch_auth_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.touch_auth_session, *args, **kwargs)

    async def delete_auth_session_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.delete_auth_session, *args, **kwargs)

    async def invalidate_other_auth_sessions_async(self, *args, **kwargs):
        return await asyncio.to_thread(self.invalidate_other_auth_sessions, *args, **kwargs)
