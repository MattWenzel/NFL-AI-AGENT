# Architecture

The app is an AI chat agent over an NFL stats SQLite database. A browser UI, an HTTP/SSE API, a model-driven tool-use loop, and two SDK adapters for Claude and GPT. Everything in this doc is about the plumbing — the NFL data layer (schemas, join graph) lives in `NFLVERSE/docs/DATABASE.md` and isn't reproduced here.

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

Top-level folders are organized by subsystem rather than layer:

```
agent/     LLM conversation domain (runtime loop, compaction, prompts)
tool/      Tool registry + handlers (SQL sandbox, schema, CSV export, ...)
auth/      Auth primitives, encryption, Codex OAuth, credential refresh
provider/  LLM adapters             (Anthropic, OpenAI, OpenAI Codex)
storage/   SQLite persistence       (RuntimeStore facade composed of mixins)
server/    HTTP transport           (FastAPI app factory, routes, schemas)
```

Dependencies flow from `server/` and `cli.py` (entry points) → `agent/` (domain) → `tool/`, `provider/`, `storage/`, `auth/` (subsystems). Subsystems don't import from `server/` or each other except where noted (e.g. `auth/codex_credentials.py` uses `storage` to persist refreshed bundles).

`cli.py` is a second entry point that drives `agent/` directly, bypassing `server/`. Same runtime, same tools; no HTTP.

| Dir | Contents | Doc |
|-----|----------|-----|
| `agent/` | `ChatRuntime`, event types, compaction, system prompt, guides | [runtime.md](runtime.md), [compaction.md](compaction.md), [prompts.md](prompts.md) |
| `tool/` | Tool definitions, registry/dispatch, validation, SQL sandbox, handlers | [tools.md](tools.md) |
| `auth/` | Password hashing, bearer-token issuance, Fernet encryption, Codex OAuth | [auth.md](auth.md) |
| `provider/` | `BaseLLMClient`, Anthropic + OpenAI + OpenAI Codex adapters, retry/overflow helpers | [providers.md](providers.md) |
| `storage/` | SQLite store (sessions, turns, tool runs, users, keys, exports) | [persistence.md](persistence.md) |
| `server/` | FastAPI app factory, routes, schemas, SSE serialization, rate limit | [transport.md](transport.md), [auth.md](auth.md) |
| `chat-ui/` | Browser app | [ui.md](ui.md) |

## Data flow of one user turn

Following a single message from the browser back to the browser:

```
 ┌── UI ─────────────────────────────┐
 │ sendMessage()                     │        chat-ui/js/streaming.js:1
 │  ├─ fetch /chat/stream            │
 │  └─ read SSE events, render       │
 └───────────────────────────────────┘
                │
                ▼
 ┌── server/routes/chat.py ────────────┐
 │ POST /chat/stream                 │        chat.py:195
 │  ├─ get_current_user (401 guard)  │
 │  ├─ _prepare_chat:                │
 │  │    ├─ IDOR check               │
 │  │    ├─ decrypt user API key     │
 │  │    ├─ create LLM client        │
 │  │    └─ prepare session          │
 │  ├─ producer/consumer queue       │
 │  │   with 15s heartbeat ping      │
 │  └─ SSE yield per RuntimeEvent    │
 └───────────────────────────────────┘
                │
                ▼
 ┌── agent/runtime.py ──────────┐
 │ ChatRuntime.run_session           │        runtime.py:83
 │  ├─ acquire session lock          │
 │  ├─ write user turn               │
 │  └─ for _ in range(10):           │
 │       ├─ compact_if_needed        │       (runtime.md, compaction.md)
 │       ├─ client.stream_message    │       (providers.md)
 │       │   ├─ TextEvent  → yield   │
 │       │   └─ ToolUseEvent → queue │
 │       ├─ if no tools: done        │
 │       ├─ raise_if_doom_loop       │
 │       ├─ asyncio.gather(tools)    │      → tool layer (tools.md)
 │       └─ loop                     │
 └───────────────────────────────────┘
                │
                │ (each tool call)
                ▼
 ┌── tool/ ───────────────────┐
 │ execute_tool_structured           │        registry.py:86
 │  ├─ validate_tool_input           │
 │  ├─ dispatch → handler            │
 │  │   └─ execute_sql → sandbox     │        sandbox.py
 │  ├─ inject_hint on known errors   │
 │  └─ return envelope (+duration)   │
 └───────────────────────────────────┘
                │
                ▼
 ┌── storage/ ──────────────────────┐
 │ RuntimeStore writes every step    │       store.py + transcripts.py
 │ (turns, parts, tool_runs)         │
 └───────────────────────────────────┘
```

Each horizontal slice is one doc in this set. Read them in that order for a first pass; cross-links carry you between them for details.

## Where things happen

Common "where does X happen" questions:

| Question | Where |
|----------|-------|
| User message arrives → HTTP | `server/routes/chat.py:195` ([transport.md](transport.md)) |
| Who owns this conversation? | IDOR guard in `_prepare_chat`, `chat.py:105` ([transport.md](transport.md#idor-protection)) |
| Which API key to use? | `resolve_user_credential` → `encryption.decrypt` ([auth.md](auth.md#api-keys)) |
| Model selects a tool | Streamed `ToolUseEvent` from the provider adapter ([providers.md](providers.md#streaming)) |
| Tool call actually runs | `ChatRuntime._execute_tool` → `execute_tool_structured` ([tools.md](tools.md#data-flow-for-one-tool-call)) |
| SQL query limits | `sandbox.py` — 500 rows, ~30s, PBP auto-attach ([tools.md](tools.md#the-sql-sandbox)) |
| Which tools are available? | `tool/definitions.py` — 7 tools ([tools.md](tools.md#the-seven-tools)) |
| What the model sees as system prompt | `get_base_prompt()` in `agent/system_prompt.py` ([prompts.md](prompts.md#the-base-prompt)) |
| Topic-specific query templates | `agent/guides/*.md`, loaded via `get_guide` tool ([prompts.md](prompts.md#guide-system)) |
| Why the conversation doesn't blow past the context window | `compact_if_needed` ([compaction.md](compaction.md)) |
| Infinite tool loops | `raise_if_doom_loop` ([runtime.md](runtime.md#doom-loop-detector)) |
| Server crash mid-turn | `finally` cleanup + `reconcile_interrupted_runs` at startup ([runtime.md](runtime.md#cleanup-on-early-exit), [persistence.md](persistence.md#startup-reconciliation)) |
| Streaming + reverse proxies | Producer/consumer + 15s heartbeat ([transport.md](transport.md#producerconsumer--heartbeat)) |
| Streaming text doesn't freeze the UI | `patchLiveText` ([ui.md](ui.md#the-patchlivetext-fast-path)) |
| How OAuth will slot in | `CLAUDE.md` + [auth.md](auth.md#oauth-migration-path-planned-not-shipped) |

## Key seams

The places where swapping a component is cheap:

### Provider adapter (`provider/`)

New LLM SDK? Subclass `BaseLLMClient`, translate canonical `Message` / `ToolUseEvent` / `TextEvent` both directions, register in `__init__.py`. Nothing in `server/` or `agent/` changes. See [providers.md](providers.md#adding-a-provider).

### Tool handler (`tool/`)

New tool? One schema in `definitions.py`, one handler function, one line in `registry.py`. Handlers are plain `(input, ctx) -> str`; no registration decorators. The drift guard catches missing entries at import time. See [tools.md](tools.md#adding-a-new-tool).

### Guide (`agent/guides/`)

New topic? Drop a markdown file, add the topic name to `_TOPICS` (`get_guide.py`) and the `enum` in `definitions.py`, add a row to the system prompt's Guide Index. No code changes. See [prompts.md](prompts.md#adding-or-changing-content).

### Transport

New entry point (CLI, MCP server, etc.)? Build a `RuntimeStore` and a `ChatRuntime`, construct a `BaseLLMClient`, call `run_session` and consume its events. The runtime is transport-agnostic — no HTTP assumptions leak into it. See `cli.py` for the minimal example.

## Concurrency model

- **Async throughout** for I/O. FastAPI + uvicorn, asyncio tools, asyncio SDK clients.
- **Per-session lock** (asyncio mutex in `RuntimeStore`) — concurrent requests to the same conversation serialize. Cross-session parallelism is untouched.
- **Parallel tool execution within a pass** via `asyncio.gather`. Handlers run in `asyncio.to_thread` so SQLite's synchronous driver doesn't stall the loop.
- **Synchronous SQLite** for both the runtime DB (one-row writes, sub-ms) and the nflverse DB (tool queries in `to_thread`). No aiosqlite — the complexity isn't worth the mostly-negligible win for this workload.
- **Single process.** Rate limiting is in-memory; no multi-worker plan without swapping that for Redis/slowapi.

## Persistence model

One SQLite file — `data/runtime.sqlite3` — holds everything mutable. Sessions, turns, assistant parts, tool runs, compaction summaries, exports, users, API keys, auth sessions. WAL mode, foreign keys on, additive migrations via `_ensure_column`. See [persistence.md](persistence.md) for the schema and the lifecycle of each record.

The nflverse databases (`nflverse.db`, `pbp.db`) are read-only reference data, attached by the SQL sandbox on demand. They never mutate at runtime and aren't backed up with user data.

## Auth model

Multi-user password auth, bearer tokens, first registrant becomes admin. Per-user API key storage via Fernet encryption. Optional invite-code gate. Rate-limited per IP. No OAuth yet; the code is shaped so OAuth slots in without disturbing the password path. See [auth.md](auth.md).

## Deployment

Single-origin — the FastAPI app serves both the API and the UI. TLS is mandatory (tokens + keys on the wire). Two documented paths:

- **Fly.io.** Dockerfile + fly.toml ship with the repo. 10GB volume for the NFL DBs. `SETTINGS_ENCRYPTION_KEY` + `REGISTRATION_INVITE_CODE` as secrets.
- **Self-hosted VPS.** Uvicorn bound to `127.0.0.1`, Caddy in front for TLS, systemd unit for supervision.

Full runbooks in `CLAUDE.md`.

## Deliberate non-features

A few things you might expect that aren't here:

- **No ORM.** Raw SQL + dataclasses in `runtime_store.py`.
- **No background jobs / task queue.** Compaction is synchronous. If it ever needs to go async, the session lock needs to coordinate with it.
- **No JWT.** Opaque bearer tokens with a DB lookup. Revocable; simpler. See [auth.md](auth.md#why-not-jwt).
- **No CSRF protection.** Token-in-header auth isn't cookie-based, so CSRF isn't a vector.
- **No UI framework.** `chat-ui/` is vanilla JS with a `state` object and a `render()` function. See [ui.md](ui.md#no-framework--why).
- **No caching layer.** Every request hits SQLite. For the current workload (personal/small-team), that's fine.
