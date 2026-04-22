# Transport

The HTTP layer is deliberately thin. Routers parse requests, resolve the authenticated user, construct a provider client, and hand off to `ChatRuntime`. The heavy lifting happens above (transport) and below (runtime + tools) — the router in between does almost nothing.

This doc covers the app factory, lifespan, per-request client construction, SSE streaming mechanics, and the IDOR guard. Auth details are in [auth.md](auth.md).

## File map

- `server/app.py` — FastAPI factory, lifespan, CORS, UI mounts.
- `server/dependencies.py` — `get_store`, `get_runtime`, `create_client_for_request`.
- `server/routes/chat.py` — `POST /chat/message` and `POST /chat/stream`.
- `server/sse.py` — `RuntimeEvent` → SSE dict serialization.
- `server/routes/conversations.py`, `providers.py`, `exports.py`, `csvs.py`, `settings.py` — other endpoints.
- `server/schemas/` — Pydantic request/response shapes.
- `auth/primitives.py` — `AuthenticatedUser`, `get_current_user` dependency.
- `run.py` — uvicorn launcher.

## App factory

`server/app.py:108`. Returns a configured `FastAPI` instance:

- Title + version for `/docs` and `/redoc`.
- `lifespan` context manager for startup/shutdown.
- CORS middleware — `ALLOWED_ORIGINS` env var replaces defaults wholesale; local defaults cover `localhost:8001` and Chrome's `null` origin for `file://` testing.
- Router mounts: `auth`, `settings`, `chat`, `conversations`, `providers`, `exports`, `csvs`.
- `/health` — single-line health endpoint for monitors and deploy preflight.
- Static mounts (see "UI wiring" below).

`app = create_app()` at module level (`app.py:167`) is what uvicorn imports. One app instance per worker process.

### CORS

`app.py:127`. `allow_credentials=False` — no cookies. The frontend uses `Authorization: Bearer <token>` on every request, so credentialed CORS isn't needed and dropping it keeps the preflight simple.

## Lifespan

`app.py:27`. Runs **once per worker process** (reload=True on dev spawns fresh workers on file change). Responsibilities:

1. **Re-apply logging.** `setup_logging()` is called here because the child process resets the root logger config.
2. **Validate encryption key.** `encryption.require_configured()` fails fast if `SETTINGS_ENCRYPTION_KEY` is missing or malformed. Failing at startup is strictly better than failing at the first `PUT /settings/api-keys` an hour later.
3. **Construct `RuntimeStore` and `ChatRuntime`.** Stored on `app.state` so request dependencies can pick them up.
4. **Purge expired auth sessions.** Housekeeping so `auth_sessions` doesn't grow unboundedly.
5. **Admin safety net.** `ensure_admin_exists` promotes a user to admin if the role was added after the DB already had users but no admin.
6. **Orphan-row sanity check.** After multi-user migration, any `NULL user_id` row is invisible to scoped queries. This check logs a warning if any exist.
7. **Log DB + provider status.** Visible proof in logs that nflverse.db and pbp.db are present, and which providers are configured (with masked key prefixes).

The store and runtime survive across requests for the life of the worker. Rebuilding them per-request would re-open SQLite handles and lose the per-session asyncio locks.

## Dependency injection

`server/dependencies.py`.

### `get_store` / `get_runtime`

`dependencies.py:40` and `:40`. Pull from `request.app.state`. If the lifespan hasn't run (test misconfiguration), raise `RuntimeError` loudly — a request trying to use a non-existent store is a programmer error, not a 500.

Usage: `store: RuntimeStore = Depends(get_store)` in router signatures.

### `create_client_for_request`

`dependencies.py:97`. Bundles three checks into one place so routers don't reimplement them:

1. Resolve provider name: request body → env `CHAT_PROVIDER` → `"anthropic"`.
2. Look up `ProviderInfo` — unknown provider → 400.
3. Verify a key exists somewhere (per-request `api_key` arg or env). If not → 503 with "add one in Settings" hint.
4. Call `create_client(provider, model, api_key)`. On `LLMError` → 503.

**The `api_key` parameter takes precedence over env vars.** The chat router looks up the authenticated user's stored Fernet-encrypted key first, decrypts it, and passes it here. Only if the user has no key does the env var `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` get used. This is what makes "personal deployment with my own key" and "multi-user deployment where everyone brings their own key" share the same code path.

### `close_client`

`dependencies.py:130`. `await client.aclose()` wrapped in a try/except that logs and moves on. A failing close shouldn't crash request teardown; worst case is a leaked httpx session until GC, which is fine for personal/small deployments.

Callers in `chat.py` wrap the runtime call in `try/finally: await close_client(client)`, so every request cleans up.

## Chat endpoints

Both live in `server/routes/chat.py`. They share `_prepare_chat` (`chat.py:88`) to resolve provider/client/session identically.

### `_prepare_chat`

1. **IDOR guard.** If `conversation_id` is supplied, it must belong to the caller. `store.get_session(conversation_id, user_id=user.id)` returns `None` on both "unknown id" and "owned by someone else", so the 404 response shape is identical either way — no existence leak.
2. **Resolve user key.** `resolve_user_credential` (`dependencies.py`) pulls the user's stored credential and returns the bearer-ready string. For classic providers it decrypts the stored API key; for OAuth providers (credential_shape="codex_oauth") it decrypts the token bundle and refreshes the access token if it's within 30s of expiring. `ValueError` on decrypt is logged and treated as "no key" so the env fallback kicks in cleanly.
3. **Create client.** `create_client_for_request(provider, model, api_key=user_key)`.
4. **Prepare session.** `runtime.prepare_session(client, provider_name, conversation_id, user_id=user.id)` — creates a new session or resolves an existing one, scoped to the user.

The caller (`/message` or `/stream`) is responsible for closing the client.

### `POST /chat/message`

`chat.py:118`. Buffered response. Iterates the runtime's async generator and collects:

- **Response text** — concatenate all `text_delta`s.
- **Tool calls** — one record per `tool_pending`, updated in place by matching `tool_run_id` on `tool_completed`/`tool_failed`. Result preview truncated to 500 chars for the response body (full result stays in the transcript).
- **Truncation flag** — set when a `runtime_error` starts with "Reached maximum tool iterations".

Non-truncation runtime errors → 500. LLM errors → 502. Everything else → 500 with stack trace in logs.

Returns `ChatResponse(conversation_id, response, tool_calls, truncated)` — a single JSON object. Used by tests and by clients that can't consume SSE.

### `POST /chat/stream`

`chat.py:195`. SSE streaming — the production path for the browser.

The wire format is standard SSE: `data: {json}\n\n` per event, with `: ping\n\n` comments for keepalives. Response headers (`chat.py:293`):

- `Cache-Control: no-cache` — prevents proxy caching.
- `Connection: keep-alive` — long-lived connection.
- `X-Accel-Buffering: no` — turns off nginx response buffering so events actually reach the client as they're produced.

### Producer/consumer + heartbeat

This is the most subtle part of the router. A naive implementation:

```python
async for event in runtime.run_session(...):
    yield f"data: {...}\n\n"
```

…works until a tool call takes more than 15–100 seconds. Reverse proxies (nginx 60s default, Cloudflare 100s) drop idle SSE connections. The client sees a truncated stream and the in-flight tool call is lost.

A not-quite-right fix:

```python
async for event in asyncio.wait_for(source, timeout=15):
    ...
```

…cancels the source generator every 15s, which cancels the tool call with it.

The correct shape — producer/consumer with a queue (`chat.py:220`):

```
┌──────────────┐            ┌──────────────┐
│  Producer    │  put()     │   Queue      │  get()   yield SSE
│  (runtime)   │─────────→  │              │  ────→   (or ping)
└──────────────┘            └──────────────┘
       │                            ↑
       │                            │
       └─ drains runtime            └─ wait_for(15s) → ping
          async for, never            on timeout
          cancelled
```

- A **producer task** runs `async for event in source` and `await queue.put(event)`. It never sees the heartbeat timeout.
- The **consumer** does `await asyncio.wait_for(queue.get(), timeout=15)`. On `TimeoutError`, emits `: ping\n\n` and loops. The producer is untouched.
- A `_PRODUCER_DONE` sentinel on the queue signals completion.
- Exceptions from the producer (`LLMError`, runtime errors, disconnects) are put on the queue and re-raised on the consumer side so the outer handler can log them.

Client disconnect is checked via `request.is_disconnected()` at the top of every loop (`chat.py:244`). On disconnect, the cleanup block (`chat.py:278`) cancels the producer task and calls `source.aclose()` — this runs the runtime's `finally` block *now*, releasing the session lock and marking in-flight tool runs as `interrupted` (see [runtime.md](runtime.md#cleanup-on-early-exit)) rather than waiting on GC.

### SSE event catalog

`server/sse.py` maps `RuntimeEvent`s to wire payloads. Events not listed are suppressed (returned as `None`):

| `RuntimeEvent.type` | Wire `type` | Extra fields |
|--------------------|-------------|--------------|
| `assistant_started` | `assistant_started` | `turn_id`, `iterations` |
| `text_delta` | `text` | `text` |
| `tool_pending` | `tool_call` | `tool_run_id`, `name`, `input` |
| `tool_completed` | `tool_result` | `tool_run_id`, `name` (no result — client fetches it from the transcript after stream ends) |
| `tool_failed` | `tool_failed` | `tool_run_id`, `name`, `message` |
| `compaction_started` | `compaction` | `meta` (compaction info dict) |
| `runtime_error` | `error` | `message` |
| `turn_started`, `assistant_requires_followup`, `turn_finished` | (suppressed) | |

The router also emits two events not tied to runtime events:

- `conversation_id` — first thing on the stream, so the client knows which session this is (matters when the request created a new one).
- `done` — emitted after `_PRODUCER_DONE` or any error path. Client uses this to distinguish "stream ended" from "connection dropped".

Notably, **tool results are not sent over SSE**. `tool_completed` carries only `tool_run_id` and `name`; the full result is in the persistent transcript. The client fetches the transcript after `done` to populate the inspector panel. This keeps SSE payloads small and avoids escaping large JSON blobs inline.

## Other routers

### `GET /chat/conversations`, `/transcript`, `PATCH`, `DELETE`

`server/routes/conversations.py`. User-scoped conversation CRUD. Every query goes through the store with `user_id=user.id`; 404 on any not-owned id.

Transcript endpoint flattens the nested `SessionTranscript` (see [persistence.md](persistence.md#sessiontranscript)) into a response shape the UI can iterate cleanly — turns include their parts and tool runs inline.

### `GET /chat/providers`

`server/routes/providers.py`. Returns `list_providers()` metadata plus an `available` flag. `available=true` if the server has the env key **or** the calling user has a stored key. This is what drives the provider dropdown's enabled/disabled state.

### `GET /exports/{filename}`

`server/routes/csv_downloads.py`. Serves generated CSVs. Path traversal guard on the filename (alphanumeric, hyphen, underscore only), ownership check against `exports.user_id`, `FileResponse` with the CSV MIME type. 404 on any not-owned file — no existence leak.

### Settings, CSVs, Auth

- `settings.py` — `GET /settings/api-keys`, `PUT /settings/api-keys/{provider}`, `DELETE /settings/api-keys/{provider}`. Fernet-encrypted storage (see [auth.md](auth.md#api-keys)).
- `csvs.py` — CSV library management: list, rename, delete, start a chat from a CSV.
- `auth.py` — register, login, logout, password change, delete account. Full detail in [auth.md](auth.md).

## UI wiring

`app.py:154`. The same FastAPI app that serves the API also serves the browser UI:

- `/static/*` → static mount of `web/static/`.
- `/` → `FileResponse(web/index.html)`.

Single-origin deployment. The UI's `<script>` and `<link>` tags use relative paths like `static/js/main.js`, and the static mount at `/static` keeps them resolving. No separate static server, no CORS between UI and API.

API routes registered above the static mount take precedence — `/health`, `/chat/*`, `/auth/*`, etc. all get matched before the root falls through to `web/index.html`.

## Running the server

`run.py`:

- Load `.env` (simple key=value parser).
- `setup_logging(verbose=args.verbose)`.
- `uvicorn.run("server.app:app", host=HOST, port=PORT, reload=True, proxy_headers=True, forwarded_allow_ips=...)`.

Key uvicorn options:

- `reload=True` — dev only; spawns fresh workers on file change. Prod should run without reload and use a process supervisor.
- `proxy_headers=True` — reads `X-Forwarded-For` / `X-Forwarded-Proto` from the reverse proxy. Without this, rate limiting (which keys on IP) would see the proxy's IP for every request and rate-limit all users together.
- `forwarded_allow_ips` — restricts who can spoof those headers. Default is loopback only; set to the proxy's IP if the proxy lives elsewhere.

Prod deployments (Fly.io, self-hosted) bind to `127.0.0.1` and put Caddy / nginx / Fly's edge in front for TLS. See [deployment.md](deployment.md) for the runbook.

## IDOR protection

Every user-facing route that accepts an id does the check in the same shape:

```python
record = store.get_X(X_id, user_id=user.id)
if record is None:
    raise HTTPException(404, "X not found")
```

`get_X(id, user_id=...)` returns `None` both when the id doesn't exist and when it exists but belongs to another user. The response is the same 404 either way, so an attacker can't distinguish "this id is invalid" from "this id is someone else's" — closes off the enumeration leak.

The IDOR guards live in the routers, not the store — the store is trusting; the store provides the scoping primitive (`user_id=?`), the router is responsible for using it on every user-facing call. There is no global enforcement mechanism, so new routes that accept ids need to remember this pattern.
