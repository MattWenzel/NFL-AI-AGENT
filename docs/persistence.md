# Persistence

Everything the runtime sees between turns is in one SQLite file — `data/runtime.sqlite3`. Sessions, turns, assistant parts, tool runs, compaction summaries, exports, users, API keys, and auth tokens. This doc covers the schema, the lifecycle of a session/turn/tool_run, startup reconciliation, and user-scoping.

The 2.3GB nflverse and pbp SQLite files are a separate concern — they're read-only reference data (see the [tools doc](tools.md#the-sql-sandbox)) and never mutate at runtime. Everything in this doc is about the runtime DB.

## File map

- `storage/` — `RuntimeStore` facade composed from `UsersMixin`, `TranscriptsMixin`, `ExportsMixin`. SQLModel-backed, async-native via aiosqlite.
  - `store.py` — facade: holds sync + async engines, per-session asyncio.Lock registry, startup hooks.
  - `models.py` — SQLModel table classes (`SessionRecord`, `TurnRecord`, `ToolRunRecord`, etc.) plus shared helpers (`utcnow`, `new_id`, `wrap_summaries_for_prompt`).
  - `engine.py` — sync engine (for one-shot Alembic + reconcile) + async engine (`sqlite+aiosqlite://`) + `async_sessionmaker`.
  - `types.py` — `TolerantJSONList` / `ToolInputJSON` TypeDecorators: malformed rows log and fall back to `[]` / `{}` instead of raising.
  - `users.py`, `transcripts.py`, `exports.py` — async mixins; every method opens its own `AsyncSession` from the store's sessionmaker.
  - `migrations/` — Alembic scaffolding. `env.py` reads the DB URL from `ALEMBIC_DATABASE_URL` or `config.RUNTIME_DB_PATH`; `versions/0001_initial_schema.py` creates all tables via `metadata.create_all` (a no-op on pre-existing DBs).
- `alembic.ini` — at repo root; points `script_location = storage/migrations`.
- `config.py` — `RUNTIME_DB_PATH` (env-overridable).

## `RuntimeStore`

`storage/store.py`. One class with one dependency (a `Path`). Responsibilities:

- Own the DB file (create if missing).
- Build the sync engine (for Alembic bootstrap + `reconcile_interrupted_runs`) and the async engine (`sqlite+aiosqlite://`) used by every CRUD method.
- Run `alembic upgrade head` on startup — creates tables on fresh DBs via the initial revision's `metadata.create_all`, stamps pre-existing DBs at `head` so future revisions apply cleanly. Both paths are idempotent.
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

The sessionmaker is built with `expire_on_commit=False` so returned ORM objects remain usable after the context closes. WAL mode and `PRAGMA foreign_keys=ON` are applied on every DBAPI connect via a SQLAlchemy `connect` event hook (`storage/engine.py`); without them, `ON DELETE CASCADE` on `auth_sessions` / `user_api_keys` silently wouldn't fire.

There's no pool sizing to tune — `aiosqlite` runs each connection on its own worker thread, and SQLite's file-level concurrency (one writer at a time in WAL) is what actually bounds throughput. For a personal deployment that's fine.

## Schema

All 9 tables are declared as SQLModel classes in `storage/models.py`. Column names, defaults, indexes, and FKs are chosen to match the DB schema byte-for-byte (modulo SQLite's dynamic typing — `VARCHAR` and `TEXT` are equivalent) so existing `runtime.sqlite3` files open without migration.

### Chat data

```
sessions                              ── one row per conversation
├─ id, created_at, updated_at
├─ provider, model, title
├─ context_window                    ── from ProviderInfo.effective_context_window
├─ pinned_at, source_csv_id          ── added post-v1 (Alembic revisions cover future moves)
└─ user_id                           ── owner; FK to users(id)

turns                                 ── one row per user/assistant/summary message
├─ id, session_id, role, status
├─ text, error
├─ input_tokens, output_tokens        ── from provider usage; drives compaction
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

Schema evolves via **Alembic revisions** under `storage/migrations/versions/`. On startup, `RuntimeStore.__init__` runs `alembic upgrade head`:

- **Fresh DB**: the initial revision's `metadata.create_all` creates every table (plus `alembic_version` for stamping).
- **Pre-existing DB** (from any prior schema): `metadata.create_all` short-circuits on `CREATE TABLE IF NOT EXISTS`, then Alembic writes the `alembic_version` row at `head`. No manual stamping needed.

Both paths are idempotent — each subsequent boot runs any pending revisions (none, on this codebase today) and otherwise no-ops.

For local schema work: `alembic revision --autogenerate -m "describe change"` generates a revision diffing `SQLModel.metadata` against the live DB. Review the generated `op.add_column` / `op.create_table` calls before committing; autogenerate is a draft, not a final answer. `ALEMBIC_DATABASE_URL` overrides the target DB for testing a revision against a throwaway file.

## Startup reconciliation

`_reconcile_interrupted_runs_sync` (`storage/store.py`), called at the end of `RuntimeStore.__init__`. Two updates via the sync engine (once per process, so sync is simpler than async here):

```python
UPDATE tool_runs  SET status = 'interrupted',
                      error = COALESCE(error, 'Tool execution interrupted by restart')
                  WHERE status IN ('pending', 'running');
UPDATE turns      SET status = 'interrupted',
                      error = COALESCE(error, 'Assistant turn interrupted by restart')
                  WHERE role = 'assistant' AND status = 'running';
```

If the server dies mid-turn — SIGKILL, OOM, power loss — the loop's `finally` cleanup (`runtime.py:314`) doesn't run. These rows would otherwise appear "running" forever in the UI. On startup, the store sweeps them to `interrupted`, logs a warning with the count, and moves on.

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

## Two sides of `create_turn`

`storage/transcripts.py:241`. Creates a turn row with the given role/status/text. Used for:

- `role='user'` with `status='completed'` — immediate write when a user message arrives.
- `role='assistant'` with `status='running'` — opened at the top of each iteration; updated to `'completed'` when the stream ends.
- `role='summary'` with `status='completed'` — by `record_compaction` and `seed_summary`. Summary turns have `compacted=0` by default; the source turns they replace are flipped to `compacted=1` in the same transaction.

There is no "streaming turn" abstraction — the turn is just a row, and `append_assistant_text` (`storage/transcripts.py:283`) concatenates chunks into the `text` column in place. If the process dies mid-stream, the partial text is preserved.

## Recovering the active prompt

`build_model_messages` (`agent/message_builder.py:12`). Walks the transcript and emits a `list[Message]` (see [providers.md](providers.md#canonical-types)) for the next model call:

1. **Summary turns first.** All non-compacted `role='summary'` turns become a single synthetic assistant message via `wrap_summaries_for_prompt` (`storage/models.py`).
2. **Then user/assistant turns in chronological order**, skipping compacted ones. For assistant turns, `tool_calls` are attached from `tool_runs_by_turn`.
3. **Then tool results** as separate `Message(role='tool_result', tool_use_id, tool_content)` entries.

Why summaries go first unconditionally is covered in [compaction.md](compaction.md#re-injecting-summaries-into-the-next-model-call).

Trailing whitespace on the final assistant content is stripped (`agent/message_builder.py:44`) — Anthropic rejects messages whose final assistant block ends with trailing whitespace, and models stream `\n` endings frequently.

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

`storage/models.py`. Single-shot snapshot returned by `get_transcript`:

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

The four reads share one connection but issue as separate autocommit statements (no explicit `BEGIN`), so inter-query consistency depends on the caller holding `conversations.lock(session_id)` for the duration of the read. Runtime callers do (lock is taken at the top of `run_session`); the HTTP transcript endpoint does not, which is tolerated — a session being actively streamed can show a half-written assistant turn until the next poll. Any future writer that bypasses the per-session lock would break this contract — either take the lock or wrap `get_transcript`'s body in a deferred transaction first.

## Exports and the `ctx` callback

`register_export` (`storage/exports.py:18`) takes the values `create_csv_export` produces and inserts an `exports` row. The owning `user_id` is resolved by looking up the session that produced the export:

```python
if source_session_id:
    sess = self.get_session(source_session_id)
    if sess is not None:
        owning_user_id = sess.user_id
```

Why through the session rather than the current user directly: the handler has no user context by design (`ctx` only carries `register_export`). Joining through the session is the store's responsibility, not the handler's.

The session lookup uses the unscoped `get_session` — no `user_id` filter. This is correct: the runtime has already validated that the user owns the session before calling the tool.

## Auth methods

A quick pointer list; full auth flow in [auth.md](auth.md):

- `create_user`, `get_user_by_email`, `get_user_by_id` — user CRUD.
- `set_user_api_key`, `get_user_api_key` — Fernet-encrypted key storage.
- `create_auth_session`, `get_auth_session`, `revoke_auth_session`, `purge_expired_sessions` — bearer-token sessions.

All of these are thin SQL wrappers. The interesting logic (password hashing, token generation, rate limiting) lives in the auth router.

## What the store deliberately doesn't do

- **Thin ORM usage.** SQLModel handles table mapping and tolerant JSON columns, but store queries stay explicit and `RuntimeStore` remains the only persistence boundary exposed to the rest of the app.
- **No caching.** Every read is a fresh query. WAL mode plus the indexes above keep this well under the network/LLM latency the user is actually waiting on.
- **No connection pool.** `_connect` opens one per call. Python's sqlite3 module is fast enough; connection pooling would add coordination with no measurable win.
- **No native async.** Python's sqlite3 is synchronous. The store exposes `_async` siblings for every public method that bridge via `asyncio.to_thread` (see `storage/store.py:48-166`), so the runtime loop, the compaction path, and FastAPI handlers never block the event loop on a SQLite call. Sync methods remain callable directly from sync contexts (startup reconciliation, tests).
