# Architecture

The app is an AI chat agent over an NFL stats SQLite database. A browser UI, an HTTP/SSE API, a model-driven tool-use loop, and three provider adapters (Anthropic, OpenAI, and OpenAI Codex via ChatGPT OAuth). Everything in this doc is about the plumbing — the NFL data layer (schemas, join graph) lives in `NFLVERSE/docs/DATABASE.md` and isn't reproduced here.

## The one-line summary

```
  browser UI  ──HTTP+SSE──▶  FastAPI  ──▶  ChatRuntime  ──▶  Provider SDKs
                                │                │                    ▲
                                ▼                ▼                    │
                         RuntimeStore       Tool handlers ─── SQL sandbox
                         (SQLite)                │
                                                 ▼
                                          nflverse.db + pbp.db
```

One user message drives one call to `ChatRuntime.run_session`, which drives the model through up to 10 iterations of tool_use → tool_result → model response. The runtime emits events; the transport wraps them as SSE; the browser renders them.

## Layering

Top-level folders are organized by product surface, then by process/capability:

```
backend/server/       FastAPI shell, dependency wiring, and feature-sliced routes
backend/services/ App-process services, schemas, DTOs, and errors
backend/lib/agent/     Chat runtime loop, turn state, events, prompts, compaction
backend/lib/providers/ LLM provider registry, shared provider types, concrete clients
backend/lib/tools/     Tool definitions, handlers, SQL sandbox, guide docs
backend/lib/db/ Runtime SQLite store, models, migrations
backend/lib/auth/ Auth/security primitives, encryption, OAuth protocol helpers
frontend/          Browser UI, grouped by app/core/feature/component ownership
```

Dependencies flow from `backend/server/routes/*.py` (entry point) → `backend/services/*/service.py` (orchestration) → `backend/*` (reusable capabilities). Services are where cross-subsystem wiring lives — decrypting a user credential, building a client, preparing a session, refreshing a Codex bundle — so routes and the agent stay focused on their own concerns.

| Dir | Contents | Doc |
|-----|----------|-----|
| `backend/server/` | FastAPI app factory, dependency wiring, middleware, routes, HTTP helpers | [transport.md](transport.md) |
| `backend/services/` | Process services, schemas, DTOs, and errors | [transport.md](transport.md), [auth.md](auth.md) |
| `backend/lib/agent/` | `ChatRuntime`, event types, compaction, system prompt, guides | [runtime.md](runtime.md), [compaction.md](compaction.md), [prompts.md](prompts.md) |
| `backend/lib/tools/` | Tool definitions, registry/dispatch, validation, SQL sandbox, handlers | [tools.md](tools.md) |
| `backend/lib/auth/` | Password hashing, bearer-token issuance, Fernet encryption, OAuth protocol helpers | [auth.md](auth.md) |
| `backend/lib/providers/` | `BaseLLMClient`, Anthropic + OpenAI + OpenAI Codex adapters, retry/overflow helpers | [providers.md](providers.md) |
| `backend/lib/db/` | Async SQLModel store + in-house `schema_version` migration runner | [persistence.md](persistence.md) |
| `frontend/` | Browser app | [ui.md](ui.md) |

## Data flow of one user turn

Following a single message from the browser back to the browser:

```
 ┌── UI ─────────────────────────────┐
 │ sendMessage()                     │        frontend/static/js/features/chat/streaming.js:1
 │  ├─ fetch /chat/stream            │
 │  └─ read SSE events, render       │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/server/routes/chat.py ─────────┐
 │ POST /chat/stream                 │        chat.py:95
 │  ├─ get_current_user (401 guard)  │
 │  ├─ acquire per-user stream slot  │
 │  ├─ delegate to ChatService       │
 │  ├─ producer/consumer queue       │
 │  │   with 15s heartbeat ping      │
 │  └─ SSE yield per RuntimeEvent    │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/services/chat/service.py ┐
 │ ChatService.prepare_chat          │        chat.py:101
 │  ├─ IDOR check                    │
 │  ├─ resolve + decrypt API key     │
 │  ├─ create_client_for_request     │
 │  └─ runtime.prepare_session       │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/lib/agent/runtime.py ───────────────┐
 │ ChatRuntime.run_session           │        runtime.py:78
 │  ├─ acquire session lock          │
 │  ├─ write user turn               │
 │  └─ for _ in range(10):           │
 │       ├─ compact_if_needed        │       (runtime.md, compaction.md)
 │       ├─ client.stream_message    │       (providers.md)
 │       │   ├─ TextEvent  → yield   │
 │       │   └─ ToolUseEvent → queue │
 │       ├─ if no tools: done        │
 │       ├─ raise_if_doom_loop       │       (backend/lib/agent/turn.py)
 │       ├─ asyncio.gather(tools)    │      → tool layer (tools.md)
 │       └─ loop                     │
 └───────────────────────────────────┘
                │
                │ (each tool call)
                ▼
 ┌── backend/lib/agent/turn.py ──────────────────┐
 │ Turn._execute_one_tool            │
 │  ├─ persist tool_run (pending)    │
 │  ├─ call execute_tool_structured  │      → backend/lib/tools/ (below)
 │  └─ persist result / error        │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/lib/tools/ ─────────────────────────┐
 │ execute_tool_structured           │        registry.py:86
 │  ├─ validate_tool_input           │
 │  ├─ dispatch → handler (to_thread)│
 │  │   └─ execute_sql → sandbox     │        sandbox.py
 │  ├─ inject_hint on known errors   │
 │  └─ return envelope (+duration)   │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/lib/db/ ───────────────────────┐
 │ RuntimeStore writes every step    │       store.py + transcript_store.py
 │ (turns, parts, tool_runs)         │       async SQLModel over aiosqlite
 └───────────────────────────────────┘
```

Each horizontal slice is one doc in this set. Read them in that order for a first pass; cross-links carry you between them for details.

## Where things happen

Common "where does X happen" questions:

| Question | Where |
|----------|-------|
| User message arrives → HTTP | `backend/server/routes/chat.py` ([transport.md](transport.md)) |
| Who owns this conversation? | IDOR guard in `ChatService.prepare_chat`, `backend/services/chat/service.py` ([transport.md](transport.md#idor-protection)) |
| Which API key to use? | `ProviderCredentialService.get_api_key` → `encryption.decrypt` ([auth.md](auth.md#api-keys)) |
| Model selects a tool | Streamed `ToolUseEvent` from the provider adapter ([providers.md](providers.md#streaming)) |
| Tool call actually runs | `Turn._execute_one_tool` → `execute_tool_structured` ([tools.md](tools.md#data-flow-for-one-tool-call)) |
| SQL query limits | `sandbox.py` — 500 rows, ~30s, PBP auto-attach ([tools.md](tools.md#the-sql-sandbox)) |
| Which tools are available? | `backend/lib/tools/definitions.py` — 7 tools ([tools.md](tools.md#the-seven-tools)) |
| What the model sees as system prompt | `get_base_prompt()` in `backend/lib/agent/system_prompt.py` ([prompts.md](prompts.md#the-base-prompt)) |
| Topic-specific query templates | `backend/lib/tools/guides/*.md`, loaded via `get_guide` tool ([prompts.md](prompts.md#guide-system)) |
| Why the conversation doesn't blow past the context window | `compact_if_needed` ([compaction.md](compaction.md)) |
| Infinite tool loops | `raise_if_doom_loop` ([runtime.md](runtime.md#doom-loop-detector)) |
| Server crash mid-turn | `finally` cleanup + `reconcile_interrupted_runs` at startup ([runtime.md](runtime.md#cleanup-on-early-exit), [persistence.md](persistence.md#startup-reconciliation)) |
| Streaming + reverse proxies | Producer/consumer + 15s heartbeat ([transport.md](transport.md#producerconsumer--heartbeat)) |
| Streaming text doesn't freeze the UI | `patchLiveText` ([ui.md](ui.md#the-patchlivetext-fast-path)) |
| How OAuth will slot in | [auth.md](auth.md#oauth-migration-path-planned-not-shipped) |

## Key seams

The places where swapping a component is cheap:

### Provider adapter (`backend/lib/providers/`)

New LLM SDK? Subclass `BaseLLMClient`, translate canonical `Message` / `ToolUseEvent` / `TextEvent` both directions, register in `__init__.py`. Nothing in `backend/server/` or `backend/lib/agent/` changes. See [providers.md](providers.md#adding-a-provider).

### Tool handler (`backend/lib/tools/`)

New tool? One schema in `definitions.py`, one handler function, one line in `registry.py`. Handlers are plain `(input, ctx) -> str`; no registration decorators. The drift guard catches missing entries at import time. See [tools.md](tools.md#adding-a-new-tool).

### Guide (`backend/lib/tools/guides/`)

New topic? Drop a markdown file into `backend/lib/tools/guides/`, add the topic name to `GUIDE_TOPICS` in `backend/lib/tools/guide_registry.py`, and add a row to the system prompt's Guide Index via `GUIDE_INDEX_ROWS`. No further code changes. See [prompts.md](prompts.md#adding-or-changing-content).

### Transport

New entry point (MCP server, background worker, etc.)? Build a `RuntimeStore`, create a runtime via `ChatRuntime(store)`, construct a `BaseLLMClient`, call `run_session`, and consume its events. The runtime is transport-agnostic — no HTTP assumptions leak into it.

## Concurrency model

- **Async throughout** for I/O. FastAPI + uvicorn, asyncio tools, asyncio SDK clients.
- **Per-session lock** (asyncio mutex in `RuntimeStore`) — concurrent requests to the same conversation serialize. Cross-session parallelism is untouched.
- **Parallel tool execution within a pass** via `asyncio.gather`.
- **Runtime DB is async** via `aiosqlite` under SQLModel. `RuntimeStore` exposes natively async methods (sessions, turns, parts, tool runs, users, keys, exports) that run on the event loop without `to_thread`. A sync engine also exists but is only used at process boot for schema migration apply and startup reconciliation (`backend/lib/db/sql/engine.py`).
- **Tool queries stay sync.** The read-only sandbox (`backend/lib/tools/sandbox/runner.py`) uses DuckDB against `nflverse.duckdb` and runs inside `asyncio.to_thread`, so a long query can't stall the loop and the driver's row-limit + timeout knobs stay available.
- **Single process.** Rate limiting is in-memory; no multi-worker plan without swapping that for Redis/slowapi.

## Persistence model

One SQLite file — `data/runtime.sqlite3` — holds everything mutable. Sessions, turns, assistant parts, tool runs, compaction summaries, exports, users, API keys, auth sessions. Tables are defined as SQLModel classes in `backend/lib/db/sql/tables.py`; the schema evolves through in-house migration callables in `backend/lib/db/sql/migrations.py`, applied automatically on process boot via `apply_migrations()`. WAL mode, foreign keys, and a 5s busy_timeout are enabled on both the sync and async engines. See [persistence.md](persistence.md) for the schema and the lifecycle of each record.

The nflverse DuckDB file (`nflverse.duckdb`) is read-only reference data opened by the SQL sandbox on demand. It never mutates at runtime and isn't backed up with user data.

## Auth model

Multi-user password auth, bearer tokens, first registrant becomes admin. Per-user API key storage via Fernet encryption. Optional invite-code gate. Rate-limited per IP. No OAuth yet; the code is shaped so OAuth slots in without disturbing the password path. See [auth.md](auth.md).

## Deployment

Single-origin — the FastAPI app serves both the API and the UI. TLS is mandatory (tokens + keys on the wire). Two documented paths:

- **Fly.io.** Dockerfile + fly.toml ship with the repo. 10GB volume for the NFL DBs. `SETTINGS_ENCRYPTION_KEY` + `REGISTRATION_INVITE_CODE` as secrets.
- **Self-hosted VPS.** Uvicorn bound to `127.0.0.1`, Caddy in front for TLS, systemd unit for supervision.

Full runbooks in [deployment.md](deployment.md).

## Deliberate non-features

A few things you might expect that aren't here:

- **No background jobs / task queue.** Compaction is synchronous. If it ever needs to go async, the session lock needs to coordinate with it.
- **No JWT.** Opaque bearer tokens with a DB lookup. Revocable; simpler. See [auth.md](auth.md#why-not-jwt).
- **No CSRF protection.** Token-in-header auth isn't cookie-based, so CSRF isn't a vector.
- **No UI framework.** `frontend/` is vanilla JS with a `state` object and a `render()` function. See [ui.md](ui.md#no-framework--why).
- **No caching layer.** Every request hits SQLite. For the current workload (personal/small-team), that's fine.
