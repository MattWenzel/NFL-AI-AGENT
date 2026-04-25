# Persistence

Everything the runtime sees between turns is in one SQLite file — `data/runtime.sqlite3`. Sessions, turns, assistant parts, tool runs, compaction summaries, exports, users, API keys, and auth tokens. This doc covers the schema, the lifecycle of a session/turn/tool_run, startup reconciliation, and user-scoping.

The 2.3GB nflverse and pbp SQLite files are a separate concern — they're read-only reference data (see the [tools doc](tools.md#the-sql-sandbox)) and never mutate at runtime. Everything in this doc is about the runtime DB.

## File map

- `backend/lib/storage/` — `RuntimeStore` facade composed from `UsersMixin`, `SessionStoreMixin`, `TranscriptStoreMixin`, `ExportsMixin`. SQLModel-backed, async-native via aiosqlite.
  - `store.py` — facade: holds sync + async engines, per-session `asyncio.Lock` registry, startup hooks (schema migration apply, reconcile).
  - `models.py` — SQLModel table classes (`SessionRecord`, `TurnRecord`, `AssistantPartRecord`, `ToolRunRecord`, `CompactionSummaryRecord`, `ExportRecord`, `UserRecord`, `UserApiKeyRecord`, `AuthSessionRecord`) plus two DTOs (`SessionListEntry`, `SessionTranscript`) and helpers (`utcnow`, `new_id`, `wrap_summaries_for_prompt`).
  - `engine.py` — builder functions for the sync engine (for one-shot migration apply + reconcile), the async engine (`sqlite+aiosqlite://`), and the `async_sessionmaker`. Both engines share one `connect` listener that applies `PRAGMA foreign_keys=ON` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` on every DBAPI connection.
  - `types.py` — `TolerantJSONList` / `ToolInputJSON` TypeDecorators: malformed rows log and fall back to `[]` / `{}` instead of raising. `ToolInputJSON` writes with `sort_keys=True` so the doom-loop detector's string-fingerprint of recent tool calls stays stable.
  - `users.py`, `session_store.py`, `transcript_store.py`, `exports.py` — async mixins; every method opens its own `AsyncSession` from the store's sessionmaker.
  - `schema_version.py` — in-house migration runner. Uses `PRAGMA user_version` as the tracker. Migrations are plain Python callables that take a sync `Connection`; `apply_migrations(engine)` runs any pending steps in a single transaction on startup. A one-shot seam reads `alembic_version` when present (DBs that predate the 2026-04-23 Alembic removal) and seeds `user_version` so no migration re-runs.
- `config.py` — `RUNTIME_DB_PATH` (env-overridable).

## `RuntimeStore`

`backend/lib/storage/store.py`. One class with one dependency (a `Path`). Responsibilities:

- Own the DB file (create if missing).
- Build the sync engine (for schema migration apply + `reconcile_interrupted_runs`) and the async engine (`sqlite+aiosqlite://`) used by every CRUD method.
- Apply schema migrations on startup via `storage.schema_version.apply_migrations()` — creates tables on fresh DBs via the baseline migration's `metadata.create_all`, seeds `user_version` from `alembic_version` for DBs that predate the Alembic removal, and applies any pending migrations in order. All paths are idempotent.
- Serve typed records (`SessionRecord`, `TurnRecord`, etc. — SQLModel classes) — no raw rows escape.
- Hand out per-session async locks (`lock(session_id)` → `asyncio.Lock`). Shared with `ChatRuntime` for turn serialization.
- Reconcile interrupted runs on startup.

Constructed once during FastAPI lifespan (see [transport.md](transport.md#lifespan)) and used as the backing store for the app's repository/runtime wiring.

## Session pattern

Every async CRUD method opens a scoped `AsyncSession`:

```python
async with self._async_session() as session:
    result = await session.execute(select(SessionRecord).where(...))
    return result.scalar_one_or_none()
```

The sessionmaker is built with `expire_on_commit=False` so returned ORM objects remain usable after the context closes. WAL mode and `PRAGMA foreign_keys=ON` are applied on every DBAPI connect via a SQLAlchemy `connect` event hook (`backend/lib/storage/engine.py`); without them, `ON DELETE CASCADE` on `auth_sessions` / `user_api_keys` silently wouldn't fire.

There's no pool sizing to tune — `aiosqlite` runs each connection on its own worker thread, and SQLite's file-level concurrency (one writer at a time in WAL) is what actually bounds throughput. For a personal deployment that's fine.

## Schema

All 9 tables are declared as SQLModel classes in `backend/lib/storage/models.py`. Column names, defaults, indexes, and FKs are chosen to match the DB schema byte-for-byte (modulo SQLite's dynamic typing — `VARCHAR` and `TEXT` are equivalent) so existing `runtime.sqlite3` files open without migration.

### Chat data

```
sessions                              ── one row per conversation
├─ id, created_at, updated_at
├─ provider, model, title
├─ context_window                    ── from ProviderInfo.effective_context_window
├─ pinned_at, source_csv_id          ── added post-v1 (schema_version migrations cover future moves)
└─ user_id                           ── owner; FK to users(id)

turns                                 ── one row per user/assistant/summary message
├─ id, session_id, role, status
├─ text, error
├─ input_tokens, output_tokens        ── from backend.lib.providers usage; drives compaction
├─ compacted                          ── 0 active, 1 dropped from prompt
└─ created_at, updated_at

assistant_parts                       ── per-block record within an assistant turn
├─ id, session_id, turn_id
├─ kind                               ── "text", "tool_call", "tool_status", "tool_result"
├─ order_index                        ── preserve stream order
├─ content, name, tool_run_id
└─ created_at

tool_runs                             ── one row per tool call
├─ id, session_id, turn_id, tool_name
├─ input                              ── dict on the Python side; canonical JSON TEXT on disk via ToolInputJSON TypeDecorator
├─ status                             ── pending → running → completed | error | interrupted
├─ result                             ── TEXT, tool output
├─ error                              ── TEXT, populated on status=error|interrupted
├─ hint, duration_ms, compacted
└─ created_at, updated_at

compaction_summaries                  ── one row per compaction event
├─ id, session_id
├─ summary_turn_id                    ── FK to the synthetic role='summary' turn
├─ source_turn_ids                    ── JSON array of turn IDs compacted into this summary
└─ created_at

exports                               ── one row per CSV generated by create_csv_export
├─ id, filename (UNIQUE), title, sql
├─ row_count, columns_json, file_size
├─ source_session_id, source_tool_run_id
├─ user_id                            ── owner
└─ created_at, updated_at
```

### Auth data

```
users                                 ── one row per account
├─ id (AUTOINCREMENT), email (UNIQUE)
├─ password_hash                      ── bcrypt; see auth.md
├─ role                               ── "admin" | "user"
├─ email_verified_at
└─ created_at, updated_at

user_api_keys                         ── encrypted per-user provider keys
├─ user_id + provider (composite PK)
├─ encrypted_key                      ── Fernet ciphertext
└─ created_at, updated_at

auth_sessions                         ── bearer token sessions
├─ token (PK)
├─ user_id                            ── FK ON DELETE CASCADE
├─ expires_at                         ── ISO 8601 UTC
└─ created_at, last_used_at
```

Indexes defined on the model classes:

| Index | Purpose |
|-------|---------|
| `idx_turns_session_created` | `get_transcript` — turns ordered within a session |
| `idx_parts_turn_order` | `get_transcript` — parts ordered within a turn |
| `idx_tool_runs_turn_created` | `get_transcript` + `get_recent_tool_runs` |
| `idx_exports_created` | export library listings |
| `idx_auth_sessions_user` | user lookup on token validation |
| `idx_sessions_user_updated` | conversations list (user-scoped, newest first) |

## Migrations

Schema evolves via **in-house migration callables** in `backend/lib/storage/schema_version.py`. On startup, `RuntimeStore.__init__` calls `apply_migrations(sync_engine)`:

- **Fresh DB** (`PRAGMA user_version = 0`, no `alembic_version` table): every migration runs. The baseline migration's `SQLModel.metadata.create_all` creates all tables in their post-migration shape; later migrations are idempotent (`CREATE TABLE IF NOT EXISTS`, guarded column renames) so they run as no-ops. `user_version` ends at `len(MIGRATIONS)`.
- **Pre-existing DB with `alembic_version`** (predates the 2026-04-23 Alembic removal): the one-shot seam reads `alembic_version.version_num`, maps it via `_ALEMBIC_VERSION_MAP`, and sets `user_version` accordingly. Pending migrations run; already-applied ones skip.
- **Steady-state DB** (`PRAGMA user_version > 0`): the seam short-circuits on the `user_version` read — the `alembic_version` table (if still present) is ignored.

All paths are idempotent. To add a new schema change: define a new `_migration_NNNN_<topic>` function that takes a `Connection`, append it to `MIGRATIONS`, and update `SQLModel` in `backend/lib/storage/models.py` to match the post-migration shape. Keep migrations idempotent (guard with `CREATE TABLE IF NOT EXISTS` or `PRAGMA table_info` checks) so fresh DBs — where `metadata.create_all` already produces the final shape — run them as no-ops.

## Startup reconciliation

`_reconcile_interrupted_runs_sync` (`backend/lib/storage/store.py`), called at the end of `RuntimeStore.__init__`. Two updates via the sync engine (once per process, so sync is simpler than async here):

```python
UPDATE tool_runs  SET status = 'interrupted',
                      error = COALESCE(error, 'Tool execution interrupted by restart')
                  WHERE status IN ('pending', 'running');
UPDATE turns      SET status = 'interrupted',
                      error = COALESCE(error, 'Assistant turn interrupted by restart')
                  WHERE role = 'assistant' AND status = 'running';
```

If the server dies mid-turn — SIGKILL, OOM, power loss — the loop's `finally` cleanup (`backend/lib/agent/runtime.py:251`) doesn't run. These rows would otherwise appear "running" forever in the UI. On startup, the store sweeps them to `interrupted`, logs a warning with the count, and moves on.

The warning matters: a restart that orphans nothing is healthy; one that orphans dozens of rows points at a crash.

## Lifecycle of one session

```
user first message → get_or_create_session(id=None)
                       ├─ INSERT into sessions
                       └─ return SessionRecord

ChatRuntime.run_session:
    ├─ create_turn(role='user', status='completed', text=user_text)
    ├─ update_session(title = user_text[:80]) on first turn
    │
    ├─ per iteration:
    │    ├─ compact_if_needed → maybe record_compaction()
    │    ├─ create_turn(role='assistant', status='running')
    │    │
    │    ├─ on TextEvent:
    │    │    ├─ append_turn_text(turn_id, chunk)
    │    │    └─ add_part(kind='text', content=chunk)
    │    │
    │    ├─ on ToolUseEvent:
    │    │    ├─ create_tool_run(status='pending', input=dict)
    │    │    └─ add_part(kind='tool_call', content=tool_call_json, tool_run_id)
    │    │
    │    ├─ update_turn(status='completed', input_tokens, output_tokens)
    │    │
    │    └─ per tool (parallel):
    │         ├─ update_tool_run(status='running')
    │         ├─ add_part(kind='tool_status', content='running')
    │         ├─ execute the tool
    │         ├─ update_tool_run(status='completed'|'error', result, hint, duration_ms)
    │         └─ add_part(kind='tool_result', content=result_content)
    │
    └─ on crash:
         finally block → update_turn(status='interrupted'), update_tool_run(status='interrupted')
```

Every event the runtime yields has a corresponding write. The transcript is append-only within a turn; the only in-place updates are on `turns` and `tool_runs` (status + result fields).

## Three roles of `create_turn`

`backend/lib/storage/conversations/transcripts.py:25`. Creates a turn row with the given role/status/text. Used for:

- `role='user'` with `status='completed'` — immediate write when a user message arrives.
- `role='assistant'` with `status='running'` — opened at the top of each iteration; updated to `'completed'` when the stream ends.
- `role='summary'` with `status='completed'` — by `record_compaction` and `seed_summary`. Summary turns have `compacted=0` by default; the source turns they replace are flipped to `compacted=1` in the same transaction.

There is no "streaming turn" abstraction — the turn is just a row, and `append_assistant_text` (`backend/lib/storage/conversations/transcripts.py:70`) concatenates chunks into the `text` column in place. If the process dies mid-stream, the partial text is preserved.

## Recovering the active prompt

`build_model_messages` (`backend/lib/agent/message_builder.py:12`). Walks the transcript and emits a `list[Message]` (see [providers.md](providers.md#canonical-types)) for the next model call:

1. **Summary turns first.** All non-compacted `role='summary'` turns become a single synthetic assistant message via `wrap_summaries_for_prompt` (`backend/lib/storage/models.py`).
2. **Then user/assistant turns in chronological order**, skipping compacted ones. For assistant turns, `tool_calls` are attached from `tool_runs_by_turn`.
3. **Then tool results** as separate `Message(role='tool_result', tool_use_id, tool_content)` entries.

Why summaries go first unconditionally is covered in [compaction.md](compaction.md#re-injecting-summaries-into-the-next-model-call).

Trailing whitespace on the final assistant content is stripped (`backend/lib/agent/message_builder.py:44`) — Anthropic rejects messages whose final assistant block ends with trailing whitespace, and models stream `\n` endings frequently.

Note: `build_model_messages` lives in the agent module, not the storage module, because it's a pure transform over `SessionTranscript` that doesn't touch SQLite — the runtime owns provider-message construction; storage owns raw persistence.

## User scoping

Every user-owned query takes a `user_id` keyword argument:

| Method | With `user_id` | Without |
|--------|----------------|---------|
| `get_session` | AND `sessions.user_id = ?` | Any session — used by internals |
| `list_sessions` | user's sessions only | All sessions — admin/maintenance |
| `delete_session` | Only if owned | Delete any — internal |
| `get_export` / `list_exports` / `get_export_by_filename` | filtered | Any |

Passing `user_id=None` bypasses the filter. This is a **trust boundary** — the HTTP layer must always pass the authenticated user's id on user-facing endpoints. See [transport.md](transport.md#idor-protection) for where this is enforced.

## `SessionTranscript`

`backend/lib/storage/models.py`. Single-shot snapshot returned by `get_transcript`:

```python
@dataclass
class SessionTranscript:
    session: SessionRecord
    turns: list[TurnRecord]                        # chronological
    parts_by_turn: dict[str, list[AssistantPartRecord]]
    tool_runs_by_turn: dict[str, list[ToolRunRecord]]
    summaries: list[CompactionSummaryRecord]
```

Four queries combined into one object. The transport layer returns this (re-shaped) to the browser for history rendering; the compaction layer iterates over it for token estimation; `build_model_messages` walks it to assemble the wire format.

The four reads share one `AsyncSession` but issue as separate autocommit statements (no explicit `BEGIN`), so inter-query consistency depends on the caller holding `store.lock(session_id)` for the duration of the read. Runtime callers do (the lock is taken at the top of `run_session`); the HTTP transcript endpoint does not, which is tolerated — a session being actively streamed can show a half-written assistant turn until the next poll. Any future writer that bypasses the per-session lock would break this contract — either take the lock or wrap `get_transcript`'s body in a deferred transaction first.

## Exports and the `ctx` callback

`register_export` (`backend/lib/storage/exports/crud.py:13`) takes the values `create_csv_export` produces and inserts an `exports` row. The owning `user_id` is resolved by looking up the session that produced the export:

```python
if source_session_id:
    sess = await self.get_session(source_session_id)
    if sess is not None:
        owning_user_id = sess.user_id
```

Why through the session rather than the current user directly: the handler has no user context by design (`ctx` only carries `register_export`). Joining through the session is the store's responsibility, not the handler's.

The session lookup uses the unscoped `get_session` — no `user_id` filter. This is correct: the runtime has already validated that the user owns the session before calling the tool.

## Auth methods

A quick pointer list; full auth flow in [auth.md](auth.md):

- `count_users`, `create_user`, `get_user_by_email`, `get_user_by_id`, `update_user_password`, `delete_user` — user CRUD (delete cascades through every user-scoped table).
- `ensure_admin_exists`, `count_orphan_rows`, `backfill_orphan_ownership` — single-tenant → multi-user migration helpers, called at startup.
- `upsert_api_key`, `get_api_key`, `list_api_keys`, `delete_api_key`, `user_has_api_key` — Fernet-encrypted per-user provider keys.
- `create_auth_session`, `get_auth_session`, `touch_auth_session`, `delete_auth_session`, `invalidate_other_auth_sessions`, `purge_expired_auth_sessions` — bearer-token sessions.

All of these are thin SQL wrappers. The interesting logic (password hashing, token generation, rate limiting) lives in the auth service and router.

## What the store deliberately doesn't do

- **No relationship-driven querying.** Tables have SQL-level foreign keys, but the SQLModel classes don't declare SQLAlchemy relationships. Callers don't navigate `session.turns` — they go through a mixin method (`get_transcript`, `list_sessions`, etc.) that issues an explicit query. This keeps the read paths predictable, makes N+1s impossible-by-construction, and keeps `RuntimeStore` as the one persistence boundary the rest of the app talks to.
- **No caching.** Every read is a fresh query. WAL mode plus the indexes above keep this well under the network/LLM latency the user is actually waiting on.
- **No connection-pool tuning.** The async engine uses SQLAlchemy's default `aiosqlite` pool with no size/timeout overrides. SQLite's file-level concurrency (one writer at a time under WAL) is what actually bounds throughput; nothing above that layer helps.
- **No sync fallback paths.** Every public method on the store is natively async — the old `*_async` wrappers are gone. The sync engine is used exclusively for two boot-time tasks (schema migration apply and the interrupted-run sweep); nothing else calls into it.
