"""RuntimeStore facade: async ORM over SQLite.

Composes per-domain mixins into one class. Every method is natively async
via an `async_sessionmaker` over aiosqlite. The old 40+ `*_async` wrappers
are gone — callers `await store.foo(...)` directly.

Startup responsibilities live here:
- Create engines (sync for bootstrap/migrations, async for runtime).
- Run `alembic upgrade head` — creates tables on fresh DBs via the
  initial revision's `metadata.create_all`, stamps pre-existing DBs at
  `head` so future revisions apply cleanly.
- Reconcile tool runs / assistant turns left mid-flight by the prior
  process (sweeps pending/running → interrupted so the transcript doesn't
  show forever-spinning rows).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import func, update

from storage.engine import build_async_engine, build_async_sessionmaker, build_sync_engine
from storage.exports import ExportsMixin
from storage.models import ToolRunRecord, TurnRecord, utcnow
from storage.transcripts import TranscriptsMixin
from storage.users import UsersMixin

logger = logging.getLogger(__name__)

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


class RuntimeStore(UsersMixin, TranscriptsMixin, ExportsMixin):
    """SQLite-backed persistence for the refactored NFL agent runtime."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_engine = build_sync_engine(db_path)
        self._async_engine = build_async_engine(db_path)
        self._async_session = build_async_sessionmaker(self._async_engine)
        self._locks: dict[str, asyncio.Lock] = {}
        self._apply_migrations()
        self._reconcile_interrupted_runs_sync()

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    # ---------------- startup helpers (sync — called once at init) ----------------

    def _apply_migrations(self) -> None:
        cfg = AlembicConfig(str(_ALEMBIC_INI))
        # `env.py` reads ALEMBIC_DATABASE_URL first so we can target this
        # specific DB file without touching the shared ini.
        os.environ["ALEMBIC_DATABASE_URL"] = f"sqlite:///{self.db_path}"
        try:
            command.upgrade(cfg, "head")
        finally:
            os.environ.pop("ALEMBIC_DATABASE_URL", None)

    def _reconcile_interrupted_runs_sync(self) -> int:
        """Mark any tool run / assistant turn left mid-flight as 'interrupted'.

        Runs once at startup via the sync engine — the previous process may
        have died during tool execution. Without this sweep those rows stay
        'pending'/'running' forever, which corrupts transcript views and
        wedges compaction selectors that skip completed-only turns.
        """
        now = utcnow()
        from sqlalchemy.orm import Session as SyncSession
        with SyncSession(self._sync_engine) as session:
            tool_update = session.execute(
                update(ToolRunRecord)
                .where(ToolRunRecord.status.in_(("pending", "running")))
                .values(
                    status="interrupted",
                    error=func.coalesce(
                        ToolRunRecord.error,
                        "Tool execution interrupted by restart",
                    ),
                    updated_at=now,
                )
            )
            count = tool_update.rowcount or 0
            session.execute(
                update(TurnRecord)
                .where(TurnRecord.role == "assistant", TurnRecord.status == "running")
                .values(
                    status="interrupted",
                    error=func.coalesce(
                        TurnRecord.error,
                        "Assistant turn interrupted by restart",
                    ),
                    updated_at=now,
                )
            )
            session.commit()
        if count:
            logger.warning("Reconciled %d interrupted tool run(s) after startup", count)
        return count

    async def reconcile_interrupted_runs(self) -> int:
        """Async wrapper for tests that simulate restarts. Production startup
        uses the sync path in `__init__` so there's no event loop assumption."""
        return await asyncio.to_thread(self._reconcile_interrupted_runs_sync)
