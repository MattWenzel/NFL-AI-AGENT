"""Engine and session factories for the runtime SQLite DB.

Two engines live side-by-side:

- **`sync_engine`** — used for one-shot bootstrap work: `create_all` on fresh
  DBs, schema-version migration apply, `reconcile_interrupted_runs` on
  startup. These run once at process start where sync is simpler than async.
- **`async_engine`** — used for all runtime CRUD via `AsyncSession`. Built on
  `aiosqlite` so every method in the store is natively async, replacing the
  prior 40+ `asyncio.to_thread` wrappers.

Both engines point at the same SQLite file. SQLite's WAL mode makes
concurrent readers + one writer safe; the per-session `asyncio.Lock` in the
store serializes conversation-level writes above that.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _enable_sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    # Under WAL, SQLITE_BUSY can still fire when two writers race the same
    # page during the single-writer window. Setting busy_timeout makes SQLite
    # retry internally instead of bubbling the error to the app.
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def build_sync_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 5})
    event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def build_async_engine(db_path: Path) -> AsyncEngine:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"timeout": 5},
    )
    # The sync-style `connect` event fires on the underlying DBAPI connection
    # for both sync and async engines; aiosqlite exposes the same callback
    # surface so foreign_keys + WAL apply uniformly.
    event.listen(engine.sync_engine, "connect", _enable_sqlite_pragmas)
    return engine


def build_async_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
