# Architecture

The app is an AI chat agent over an NFL stats database. A React browser UI, an HTTP/SSE API, a model-driven tool-use loop, and three provider adapters (Anthropic, OpenAI, and OpenAI Codex via ChatGPT OAuth). Two databases are involved: `nflverse.duckdb` (read-only DuckDB reference data — the NFL stats themselves, never mutated at runtime) and `data/runtime.sqlite3` (SQLite via aiosqlite + SQLModel — sessions, turns, parts, tool runs, table-state, exports, users, identities, etc.). Everything in this doc is about the plumbing — the NFL data layer (schemas, join graph) lives in `NFLVERSE/docs/DATABASE.md` and isn't reproduced here.

## The one-line summary

```
  browser UI  ──HTTP+SSE──▶  FastAPI  ──▶  ChatRuntime  ──▶  Provider SDKs
                                │                │                    ▲
                                ▼                ▼                    │
                         RuntimeStore       Tool handlers ─── SQL sandbox
                         (SQLite — runtime)       │
                                                  ▼
                                       nflverse.duckdb (read-only)
```

One user message drives one call to `ChatRuntime.run_session`, which drives the model through up to 10 iterations of tool_use → tool_result → model response. The runtime emits events; the transport wraps them as SSE; the browser renders them.

## Layering

Top-level folders are organized by product surface, then by process/capability:

```
backend/api/              FastAPI HTTP boundary — routes, Pydantic wire DTOs, Depends factories
backend/application/      Use-case services (one per HTTP feature), with service-internal types and errors
backend/domain/agent/     Chat runtime loop, turn state, events, prompts, compaction
backend/domain/providers/ LLM provider registry, shared provider types, concrete clients
backend/domain/tools/     Tool definitions, handlers, SQL sandbox, guide docs
backend/domain/auth/      Auth/security primitives, encryption, OAuth protocol helpers
backend/data/             Persistence — SQLite store, models, repositories, migrations (the SQL boundary)
backend/server/           HTTP infrastructure — app factory, middleware, csrf, session, sse, startup
frontend/                 Browser UI, grouped by app/core/feature/component ownership
```

Dependencies flow from `backend/api/routes/*.py` (entry point) → `backend/application/*/service.py` (orchestration) → `backend/domain/*` and `backend/data/*` (reusable capabilities). Services are where cross-subsystem wiring lives — decrypting a user credential, building a client, preparing a session, refreshing a Codex bundle — so routes and the agent stay focused on their own concerns.

| Dir | Contents | Doc |
|-----|----------|-----|
| `backend/api/` | Routes, Pydantic wire DTOs, FastAPI `Depends` factories | [transport.md](transport.md) |
| `backend/application/` | Process services, service-internal types, and errors | [transport.md](transport.md), [auth.md](auth.md) |
| `backend/domain/agent/` | `ChatRuntime`, event types, compaction, system prompt, guides | [runtime.md](runtime.md), [compaction.md](compaction.md), [prompts.md](prompts.md) |
| `backend/domain/tools/` | Tool definitions, registry/dispatch, validation, SQL sandbox, handlers | [tools.md](tools.md) |
| `backend/domain/auth/` | Password hashing, bearer-token issuance, Fernet encryption, OAuth protocol helpers | [auth.md](auth.md) |
| `backend/domain/providers/` | `BaseLLMClient`, Anthropic + OpenAI + OpenAI Codex adapters, retry/overflow helpers | [providers.md](providers.md) |
| `backend/data/` | Async SQLModel store + repositories + in-house migration runner | [persistence.md](persistence.md) |
| `backend/server/` | App factory, middleware (request-id, security headers), CSRF, session, SSE, startup | [transport.md](transport.md) |
| `frontend/` | Browser app — React + Vite + Tailwind + shadcn/ui | [ui.md](ui.md) |

## Data flow of one user turn

Following a single message from the browser back to the browser:

```
 ┌── UI ─────────────────────────────┐
 │ sendMessage()                     │        frontend/src/lib/chatStore.ts
 │  ├─ fetch /chat/stream            │
 │  └─ read SSE events, render       │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/api/routes/chat.py ─────────┐
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
 ┌── backend/application/chat.py ┐
 │ ChatService.prepare_chat          │        chat.py:101
 │  ├─ IDOR check                    │
 │  ├─ resolve + decrypt API key     │
 │  ├─ create_client_for_request     │
 │  └─ runtime.prepare_session       │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/domain/agent/runtime.py ───────────────┐
 │ ChatRuntime.run_session           │        runtime.py:78
 │  ├─ acquire session lock          │
 │  ├─ write user turn               │
 │  └─ for _ in range(10):           │
 │       ├─ compact_if_needed        │       (runtime.md, compaction.md)
 │       ├─ client.stream_message    │       (providers.md)
 │       │   ├─ TextEvent  → yield   │
 │       │   └─ ToolUseEvent → queue │
 │       ├─ if no tools: done        │
 │       ├─ raise_if_doom_loop       │       (backend/domain/agent/turn.py)
 │       ├─ asyncio.gather(tools)    │      → tool layer (tools.md)
 │       └─ loop                     │
 └───────────────────────────────────┘
                │
                │ (each tool call)
                ▼
 ┌── backend/domain/agent/turn.py ──────────────────┐
 │ Turn._execute_one_tool            │
 │  ├─ persist tool_run (pending)    │
 │  ├─ call execute_tool_structured  │      → backend/domain/tools/ (below)
 │  └─ persist result / error        │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/domain/tools/ ─────────────────────────┐
 │ execute_tool_structured           │        registry.py:86
 │  ├─ validate_tool_input           │
 │  ├─ dispatch → handler (to_thread)│
 │  │   └─ execute_sql → sandbox     │        sandbox/runner.py
 │  ├─ append hint on known errors  │
 │  └─ return envelope (+duration)   │
 └───────────────────────────────────┘
                │
                ▼
 ┌── backend/data/ ───────────────────────┐
 │ RuntimeStore writes every step    │       store.py + repositories/conversations/
 │ (turns, parts, tool_runs)         │       async SQLModel over aiosqlite
 └───────────────────────────────────┘
```

Each horizontal slice is one doc in this set. Read them in that order for a first pass; cross-links carry you between them for details.

## Where things happen

Common "where does X happen" questions:

| Question | Where |
|----------|-------|
| User message arrives → HTTP | `backend/api/routes/chat.py` ([transport.md](transport.md)) |
| Who owns this conversation? | IDOR guard in `ChatService.prepare_chat`, `backend/application/chat.py` ([transport.md](transport.md#idor-protection)) |
| Which API key to use? | `ProviderCredentialService.get_api_key` → `encryption.decrypt` ([auth.md](auth.md#api-keys)) |
| Model selects a tool | Streamed `ToolUseEvent` from the provider adapter ([providers.md](providers.md#streaming)) |
| Tool call actually runs | `Turn._execute_one_tool` → `execute_tool_structured` ([tools.md](tools.md#data-flow-for-one-tool-call)) |
| SQL query limits | `sandbox/runner.py` — 500 rows, ~30s, PBP auto-attach ([tools.md](tools.md#the-sql-sandbox)) |
| Which tools are available? | `backend/domain/tools/registry.py` — 10 tools, one module each ([tools.md](tools.md#the-ten-tools)) |
| Where the Database tab's helper chat lives | `backend/domain/agent/stateless.py` + `backend/application/db_helper_chat.py` ([database-browser.md](database-browser.md)) |
| Where Reports state lives | `backend/application/tables.py` (table_chat sessions, `TableStateRecord`) ([persistence.md](persistence.md)) |
| What the model sees as system prompt | `get_base_prompt()` in `backend/domain/agent/system_prompt.py` ([prompts.md](prompts.md#the-base-prompt)) |
| Topic-specific query templates | `backend/domain/tools/guides/*.md`, loaded via `get_guide` tool ([prompts.md](prompts.md#guide-system)) |
| Why the conversation doesn't blow past the context window | `compact_if_needed` ([compaction.md](compaction.md)) |
| Infinite tool loops | `raise_if_doom_loop` ([runtime.md](runtime.md#doom-loop-detector)) |
| Server crash mid-turn | `finally` cleanup + `reconcile_interrupted_runs` at startup ([runtime.md](runtime.md#cleanup-on-early-exit), [persistence.md](persistence.md#startup-reconciliation)) |
| Streaming + reverse proxies | Producer/consumer + 15s heartbeat ([transport.md](transport.md#producerconsumer--heartbeat)) |
| Streaming text doesn't freeze the UI | `patchLiveText` ([ui.md](ui.md#the-patchlivetext-fast-path)) |
| How OAuth will slot in | [auth.md](auth.md#oauth-migration-path-planned-not-shipped) |

## Key seams

The places where swapping a component is cheap:

### Provider adapter (`backend/domain/providers/`)

New LLM SDK? Subclass `BaseLLMClient`, translate canonical `Message` / `ToolUseEvent` / `TextEvent` both directions, register in `__init__.py`. Nothing in `backend/server/` or `backend/domain/agent/` changes. See [providers.md](providers.md#adding-a-provider).

### Tool handler (`backend/domain/tools/`)

New tool? One module exporting `TOOL = Tool(...)` (schema + handler together), one line in `registry.py`'s catalog. Handlers are plain `(input, ctx) -> str`; no registration decorators, and no second table to drift against. See [tools.md](tools.md#adding-a-new-tool).

### Guide (`backend/domain/tools/guides/`)

New topic? Drop a markdown file into `backend/domain/tools/guides/`, add the topic name to `GUIDE_TOPICS` in `backend/domain/tools/guide_registry.py`, and add a row to the system prompt's Guide Index via `GUIDE_INDEX_ROWS`. No further code changes. See [prompts.md](prompts.md#adding-or-changing-content).

### Transport

New entry point (MCP server, background worker, etc.)? Build a `RuntimeStore`, create a runtime via `ChatRuntime(store)`, construct a `BaseLLMClient`, call `run_session`, and consume its events. The runtime is transport-agnostic — no HTTP assumptions leak into it.

### Stateless agent loop

For UIs that don't need persistence (the Database tab's helper chat is the only current consumer), `backend/domain/agent/stateless.py::run_stateless_turn` is an alternate path: same provider clients, same tool registry, same SSE event types — but no `ChatRuntime`, no `RuntimeStore`, no compaction. Caller passes the full message list each turn; nothing is written back. See [database-browser.md](database-browser.md).

## Concurrency model

- **Async throughout** for I/O. FastAPI + uvicorn, asyncio tools, asyncio SDK clients.
- **Per-session lock** (asyncio mutex in `RuntimeStore`) — concurrent requests to the same conversation serialize. Cross-session parallelism is untouched.
- **Parallel tool execution within a pass** via `asyncio.gather`.
- **Runtime DB is async** via `aiosqlite` under SQLModel. `RuntimeStore` exposes natively async methods (sessions, turns, parts, tool runs, users, keys, exports) that run on the event loop without `to_thread`. A sync engine also exists but is only used at process boot for schema migration apply and startup reconciliation (`backend/data/database.py`).
- **Tool queries stay sync.** The read-only sandbox (`backend/domain/tools/sandbox/runner.py`) uses DuckDB against `nflverse.duckdb` and runs inside `asyncio.to_thread`, so a long query can't stall the loop and the driver's row-limit + timeout knobs stay available.
- **Single process.** Rate limiting is in-memory; no multi-worker plan without swapping that for Redis/slowapi.

## Persistence model

One SQLite file — `data/runtime.sqlite3` — holds everything mutable. Sessions, turns, assistant parts, tool runs, compaction summaries, exports, users, API keys, auth sessions. Tables are defined as SQLModel classes in `backend/data/models.py`; the schema evolves through in-house migration callables in `backend/data/migrations.py`, applied automatically on process boot via `apply_migrations()`. WAL mode, foreign keys, and a 5s busy_timeout are enabled on both the sync and async engines. See [persistence.md](persistence.md) for the schema and the lifecycle of each record.

The nflverse DuckDB file (`nflverse.duckdb`) is read-only reference data opened by the SQL sandbox on demand. It never mutates at runtime and isn't backed up with user data.

## Auth model

Multi-user password auth + Google OAuth + Sign-in-with-ChatGPT (Codex device flow). First registrant becomes admin. Per-user API key storage via Fernet encryption. Optional invite-code gate. Rate-limited per IP. Browser sessions ride an `HttpOnly` cookie + JS-readable CSRF cookie (double-submit on mutating requests); Bearer tokens still work for API clients. See [auth.md](auth.md).

## Deliberate non-features

A few things you might expect that aren't here:

- **No background jobs / task queue.** Compaction is synchronous. If it ever needs to go async, the session lock needs to coordinate with it.
- **No JWT.** Opaque bearer tokens with a DB lookup. Revocable; simpler. See [auth.md](auth.md#why-not-jwt).
- **No caching layer.** Every request hits SQLite (runtime store) or DuckDB (NFL data). For the current workload (personal/small-team), that's fine.
- **No multi-process scaling.** Rate limits, OAuth pending-flow registry, and the per-session lock registry all live in process memory. Scaling past one machine means swapping those for Redis (or equivalent). DuckDB also won't open a shared file from multiple processes.
