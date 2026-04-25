# Transport

The HTTP layer is three thin bands: **routes** parse requests and translate exceptions, **services** orchestrate cross-subsystem work (IDOR, credential decrypt, client construction, session prep), and the agent/storage subsystems do the actual work. This doc covers all three, plus the lifespan, SSE streaming mechanics, rate limiting, and the IDOR model. Auth details are in [auth.md](auth.md).

## File map

- `backend/api/app.py` — FastAPI factory, CORS, lifespan, router includes, static mount.
- `backend/api/routes/*.py` — FastAPI routers by app process.
- `backend/api/dependencies.py` — FastAPI dependency factories.
- `backend/api/csrf.py`, `backend/api/session.py`, `backend/api/request_context.py` — HTTP boundary helpers.
- `backend/api/sse.py` — `RuntimeEvent` → SSE dict serialization.
- `backend/processes/*/service.py` — application services by app process.
- `backend/processes/*/schemas.py` — Pydantic wire models by app process.
- `backend/api/` — startup, API-owned process state, rate limiting, and logging.
- `backend/runtime_state.py` — framework-free lock registries and pending OAuth flow registries.
- `backend/security/primitives.py` — password hashing and token generation.
- `backend/security/types.py` — auth/OAuth value objects.
- `run.py` — uvicorn launcher.

## App factory

`backend/api/app.py`. `create_app()` returns a configured `FastAPI` instance:

- Title + version for `/docs` and `/redoc`.
- `lifespan` context manager (below).
- CORS middleware (`app.py:65`) — `ALLOWED_ORIGINS` env var replaces the dev defaults wholesale. Local defaults cover `localhost:8001` and Chrome's `null` origin (for `file://` testing). `allow_credentials=False` — no cookies; the frontend sends `Authorization: Bearer <token>` on every request, so credentialed CORS isn't needed.
- Eight router mounts (`app.py:74`): auth, settings, Google OAuth, chat, conversations, providers, CSV downloads, CSV library.
- `/health` — single-line health endpoint (`app.py:83`).
- Static mount (`app.py:91`): `/static/*` → `frontend/static/`; `/` → `FileResponse(frontend/index.html)`.

Module-level `app = create_app()` is what uvicorn imports. One app instance per worker process.

## Lifespan

`app.py:26`. Runs **once per worker process**. The body delegates to four helpers in `backend/api/startup.py`:

1. **Re-apply logging.** `setup_logging()` runs inside the worker because the child process resets the root logger when `reload=True` is in use.
2. **`validate_encryption()`** (`startup.py`). Calls `encryption.require_configured()` — fails fast if `SETTINGS_ENCRYPTION_KEY` is missing or malformed. Failing at startup is strictly better than at the first `PUT /settings/api-keys` an hour later.
3. **`configure_runtime_state(app)`**. Builds `RuntimeStore(RUNTIME_DB_PATH)`, `ChatRuntime(store)`, and `AppProcessState()`; attaches them to `app.state` so request dependencies can pick them up.
4. **`await run_housekeeping(app)`**. `purge_expired_auth_sessions`, `ensure_admin_exists` (promotes a user if the role was added to an already-seeded DB), and `count_orphan_rows` (logs a warning for any `NULL user_id` rows invisible to scoped queries).
5. **`log_environment_state()`**. Logs DB paths + file sizes (nflverse.db, pbp.db, runtime DB) and which providers are configured, with masked key prefixes. Visible proof in logs that everything's in place.

Shutdown (`app.py:40`): `await app.state.process_state.aclose()` cancels any in-flight Codex OAuth device-flow background tasks so they don't leak past the worker's lifetime.

The store, runtime, and process state survive across requests for the life of the worker. Rebuilding them per-request would re-open SQLite handles and lose the per-session asyncio locks, per-user OAuth refresh locks, and stream-gate counts.

## Services layer

Routes in `backend/api/routes/` are thin shells — parse the request, call one service method, translate service exceptions to HTTP status codes. Everything cross-subsystem lives in `backend/processes/`. Each service is a class instantiated per-request via a `Depends(...)` factory (see below) with whatever it needs from the store, runtime, and process state.

| Service | File | What it does |
|---------|------|--------------|
| `ChatService` | `backend/processes/chat/service.py` | `prepare_chat` (IDOR, decrypt credential, build client, prepare session), `run_message` (buffered response), `stream_events` (SSE event source). |
| `ConversationService` | `backend/processes/conversations/service.py` | List / get-transcript / update-title-or-pin / delete for the authenticated user's conversations. |
| `AuthService` | `backend/processes/auth/service.py` | Register, login, logout, password change, delete account. Password hashing + token issuance live here; routes only translate exceptions. |
| `ProviderCredentialService` | `backend/processes/oauth/credentials.py` | Resolves the per-user API key for one provider. Dispatches Codex OAuth to the Codex credential helper; plain API keys are decrypted directly. |
| `CodexOAuthService` | `backend/processes/oauth/codex/service.py` | Device-code flow: `start`, `status`, `cancel`. Spawns a background task that polls OpenAI's device endpoint and stores the encrypted bundle on success. |
| `ExportService` | `backend/processes/exports/service.py` | List / preview / rename / delete CSV exports + seed a new conversation from one. IDOR at each entry point. |
| `ProviderService` | `backend/processes/providers/service.py` | Provider availability (server config ∪ user keys). |
| `SettingsService` | `backend/processes/settings/service.py` | Per-provider API-key status and set/clear key operations. |

Services own **IDOR enforcement** — every one that accepts an id passes the authenticated user's id through to the store so unowned records return `None` and surface as 404. Routes rely on this; they don't re-check.

## Dependency injection

`backend/api/dependencies.py`. Every request dependency lives here.

**Core** (pull from `app.state`; raise `RuntimeError` if the lifespan didn't run):

- `get_store` (`dependencies.py:28`) → `RuntimeStore`
- `get_runtime` (`dependencies.py:80`) → `ChatRuntime`
- `get_process_state` (`dependencies.py:89`) → `AppProcessState`

**Auth**:

- `get_current_user` (`dependencies.py:59`) — extracts bearer token, looks up session, validates expiry, touches `last_used_at` (throttled by `AUTH_SESSION_TOUCH_INTERVAL_SECONDS`), returns `AuthenticatedUser`. 401 on failure.
- `get_current_user_optional` — same but returns `None` instead of raising. Used by `GET /auth/status`.

**Service factories** (one per service):

- `get_chat_service` (`dependencies.py:114`) — `ChatService(runtime, store, refresh_locks)`.
- `get_conversation_service`, `get_codex_oauth_service`, `get_export_service`, `get_auth_service`, `get_settings_service`.

Routes that need rate limits or process-local coordination depend on
`get_process_state` and read the specific limiter/registry from
`AppProcessState`.

## Chat endpoints

Routes live in `backend/api/routes/chat.py` and delegate to `ChatService` in `backend/processes/chat/service.py`.

### `POST /chat/message` — buffered response

`chat.py:64`. Dev/test path for clients that can't consume SSE. `await service.run_message(body, user, tools=TOOLS)` streams internally and aggregates:

- Concatenated response text from every `text_delta`.
- One `ToolCallPreview` per `tool_pending`, updated by `tool_run_id` on `tool_completed`/`tool_failed`. Result preview truncated to 500 chars (full result stays in the transcript).
- `truncated=True` when a `runtime_error` starts with "Reached maximum tool iterations".

Exception translation: `ChatNotFoundError → 404`, `ChatConfigurationError → 503`, `ChatServiceError → 500`, `LLMError → 502`, everything else → 500.

Returns `ChatResponse(conversation_id, response, tool_calls, truncated)`.

### `POST /chat/stream` — SSE (production path)

`chat.py:95`. Streaming response for the browser.

Standard SSE wire format: `data: {json}\n\n` per event, with `: ping\n\n` comments for keepalives. Response headers (`chat.py:216`):

- `Cache-Control: no-cache` — prevents proxy caching.
- `Connection: keep-alive` — long-lived connection.
- `X-Accel-Buffering: no` — turns off nginx response buffering so events actually reach the client as they're produced.

**All resource acquisition happens inside the event generator, not in the route body.** If we reserved the stream slot or opened the client *before* returning `StreamingResponse` and the client disconnected between response-start and first body send, the generator's `finally` would never run and both would leak. With acquisition deferred, an un-iterated generator holds nothing.

Acquisition failures that used to surface as HTTP status codes (429 on stream cap, 404/503 from `prepare_chat`) now emit a structured SSE `{type: "error", code, status, message}` followed by `{type: "done"}`. HTTP is always 200 once the response returns; the frontend handles `{"type": "error"}` uniformly with runtime errors.

#### Per-user stream cap

`process_state.chat_stream_limiter.max_active` is enforced inside the event
generator. A fourth concurrent `/chat/stream` from the same user yields a
`rate_limited` SSE error + `done` — not HTTP 429.

#### Producer/consumer + heartbeat

This is the subtle part. A naive `async for event in runtime.run_session(...)` works until a tool call takes 15–100s — reverse proxies (nginx 60s, Cloudflare 100s) drop idle SSE connections. A naive `asyncio.wait_for(source, timeout=15)` cancels the runtime generator, which cancels the in-flight tool with it.

Correct shape (`chat.py:150`):

```
┌──────────────┐   put()   ┌──────────────┐
│  Producer    │────────→  │   Queue      │──→  consumer yields SSE
│  (runtime)   │           │              │        or emits : ping
└──────────────┘           └──────────────┘
       │                          ↑
       └─ async for,              └─ wait_for(15s) → ping on timeout
          never cancelled
```

- A **producer task** runs `async for event in service.stream_events(...)` and `await queue.put(event)`. It never sees the heartbeat timeout.
- The **consumer** does `await asyncio.wait_for(queue.get(), timeout=15)`. On `TimeoutError`, yields `: ping\n\n` and loops. The producer is untouched.
- A `_PRODUCER_DONE` sentinel on the queue signals completion.
- Exceptions from the producer (`LLMError`, runtime errors, disconnects) are put on the queue and re-raised on the consumer side so the outer handler can log them.

Client disconnect: `request.is_disconnected()` is checked at the top of every loop iteration. On disconnect, the cleanup block cancels the producer task and calls `source.aclose()` — this runs the runtime's `finally` block *now*, releasing the session lock and flipping in-flight tool runs to `interrupted` (see [runtime.md](runtime.md#cleanup-on-early-exit)) rather than waiting on GC.

## SSE event catalog

`backend/api/sse.py` maps `RuntimeEvent`s to wire payloads. Events not listed are suppressed (mapper returns `None`):

| `RuntimeEvent.type` | Wire `type` | Extra fields |
|---------------------|-------------|--------------|
| `assistant_started` | `assistant_started` | `turn_id`, `iterations` |
| `text_delta` | `text` | `text` |
| `tool_pending` | `tool_call` | `tool_run_id`, `name`, `input` |
| `tool_completed` | `tool_result` | `tool_run_id`, `name` (result lives in the transcript) |
| `tool_failed` | `tool_failed` | `tool_run_id`, `name`, `message` |
| `compaction_started` | `compaction` | `meta` |
| `retrying` | `retrying` | `attempt`, `delay_seconds`, `message` |
| `runtime_error` | `error` | `message` |
| `turn_started`, `turn_finished`, `assistant_requires_followup` | (suppressed) | |

The route also emits three events outside the mapper:

- `conversation_id` — the very first event on the stream. Matters when the request created a new session; the client reads this to wire the conversation into the UI.
- `error` — for acquisition-time failures (rate-limit, not-found, config). Always followed by `done`.
- `done` — emitted after `_PRODUCER_DONE` or any error path. The client uses this to distinguish "stream ended cleanly" from "connection dropped".

**Tool results do not ride SSE.** `tool_completed` carries only `tool_run_id` and `name`; the full result sits in the persistent transcript. The client fetches it after `done` to populate the inspector panel. This keeps SSE payloads small and avoids escaping large JSON blobs inline.

## Other routes

Routes are in `backend/api/routes/`. Every user-scoped endpoint depends on `get_current_user`; auth, health, and root do not.

**Auth** (`routes/auth.py`). `GET /auth/status` (optional auth), `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `PUT /auth/password`, `DELETE /auth/me`. Rate-limited per IP (see below). Full flow in [auth.md](auth.md).

**Conversations** (`routes/conversations.py`). `GET /chat/conversations`, `GET /chat/conversations/{id}/transcript`, `PATCH /chat/conversations/{id}` (title/pinned), `DELETE /chat/conversations/{id}`. Transcript endpoint flattens the nested `SessionTranscript` (see [persistence.md](persistence.md#sessiontranscript)) into a response shape the UI can iterate cleanly.

**Providers** (`routes/providers.py`). `GET /chat/providers` — returns the provider list with `available=true` iff the server has an env key **or** the calling user has a stored key. Drives the provider dropdown's enabled/disabled state in the UI.

**Settings** (`routes/settings.py`). `GET /settings/api-keys`, `PUT /settings/api-keys/{provider}`, `DELETE /settings/api-keys/{provider}` — Fernet-encrypted per-user keys. `PUT` refuses to accept a raw Codex OAuth key (must go through the device flow).

**Codex OAuth** (`routes/settings.py`). `POST /settings/oauth/codex/start` (rate-limited 5/hour/IP), `GET /settings/oauth/codex/status`, `DELETE /settings/oauth/codex/cancel`. Device-code flow — returns a `user_code` and verification URL, polls OpenAI's device endpoint in a background task, persists the encrypted OAuth bundle on success. See [auth.md](auth.md#chatgpt-oauth).

**CSV library** (`routes/exports.py`). `GET /chat/exports`, `GET /chat/exports/{id}` (preview + metadata), `PATCH /chat/exports/{id}` (rename), `DELETE /chat/exports/{id}`, `POST /chat/exports/{id}/new-session` (seed a fresh conversation with rows from this export). All IDOR-scoped.

**CSV downloads** (`routes/exports.py`, `download_router`). `GET /exports/{filename}` — serves the generated CSV. Filename safe-char check (alphanumeric / hyphen / underscore), ownership check against `exports.user_id`, then `FileResponse` with CSV MIME. 404 on any not-owned file — no existence leak.

## Rate limiting and concurrency

`backend/api/rate_limit.py`. Two primitives, both in-memory (single-process only). API-facing limiter state lives on `AppProcessState` in `backend/api/process_state.py` so it's shared across requests within one worker. Framework-free lock and pending-flow registries live in `backend/runtime_state.py`.

**`RateLimiter`** — sliding-window counter keyed by `request.client.host`. Per-IP bucket of request timestamps; entries older than the window are pruned. Infrequent global prune reaps empty buckets. Used by auth + Codex OAuth:

| Limiter | Limit | Scope |
|---------|-------|-------|
| `register_limiter` | 5 / 15 min | per IP |
| `login_limiter` | 10 / 15 min | per IP |
| `account_limiter` (password change, delete account) | 20 / 15 min | per IP |
| `codex_start_limiter` (`POST /settings/oauth/codex/start`) | 5 / hour | per IP |

**`ConcurrencyLimiter`** — dict of `key → int` guarded by an `asyncio.Lock`. `acquire(key)` increments; raises if at `max_active`. `release(key)` decrements. Used by:

| Limiter | Limit | Scope |
|---------|-------|-------|
| `chat_stream_limiter` | 3 active | per user |

Multi-worker deployments would need Redis/slowapi in place of both. The single-process design is intentional — easy to reason about, fast enough for personal/small-team scale.

## Codex OAuth flow (overview)

The one place where the transport layer stands up long-lived background work. Full protocol in [auth.md](auth.md#chatgpt-oauth); the transport-side shape:

1. `POST /settings/oauth/codex/start` — `CodexOAuthService.start(user_id=...)` evicts stale flows, calls OpenAI's `request_device_code`, and creates a `PendingCodexOAuthFlow` in the in-memory registry. Spawns `run_device_flow(pending_id)` as an asyncio task; returns the `user_code` + `verification_url` immediately.
2. Browser polls `GET /settings/oauth/codex/status` — reads the flow's status from the registry: `pending` / `complete` / `expired` / `error`.
3. Background task polls OpenAI until the user completes the flow. On success, stores the encrypted OAuth bundle via `UsersMixin.upsert_api_key` (credential_shape="codex_oauth"), updates flow status to `complete`.
4. `DELETE /settings/oauth/codex/cancel` — cancels the background task.

On worker shutdown, `AppProcessState.aclose()` cancels every in-flight flow task so we don't leak asyncio tasks past the lifespan.

Once connected, `codex_credentials.resolve_access_token(user_id)` handles refresh-if-near-expiry under a per-user lock from `codex_refresh_locks`. This lock serializes refreshes when multiple concurrent requests from the same user all hit a near-expiry token at once.

## IDOR protection

Every user-scoped endpoint's ownership check happens inside the **service**, not in the route:

```python
# backend/processes/chat/service.py
if (
    body.conversation_id
    and await self.store.get_session(body.conversation_id, user_id=user.id) is None
):
    raise ChatNotFoundError("Conversation not found")
```

`store.get_session(id, user_id=?)` returns `None` for both "unknown id" and "owned by someone else" — the 404 response is identical either way, so an attacker can't distinguish invalid ids from other users' ids (no enumeration leak).

The store is the scoping primitive — every user-facing method accepts a `user_id` kwarg. Services are responsible for passing it on every call. Routes rely on the service; they don't re-check.

There's **no framework-level enforcement** — a new service method that accepts an id must remember the `user_id=user.id` filter. If `RuntimeStore` grows a new scoped table, add a `user_id` filter from the start.

## UI wiring

Same FastAPI app serves the browser UI:

- `/static/*` → `StaticFiles(directory=frontend/static)`.
- `/` → `FileResponse(frontend/index.html)`.

Single-origin deployment. The UI's `<script src="static/js/app/main.js">` and `<link href="static/css/...">` tags resolve against the static mount. No separate static server, no CORS between UI and API.

API routes registered above the static mount take precedence — `/health`, `/chat/*`, `/auth/*`, etc. all match before the root falls through to `frontend/index.html`.

## Running the server

`run.py`:

- `load_dotenv()` — pulls `.env` into the process environment.
- `setup_logging(verbose=args.verbose)` — DEBUG when `--verbose`, else WARNING. `NFLVERSE_VERBOSE=1` propagates into the reloaded worker process.
- `uvicorn.run("backend.api.app:app", host=HOST, port=PORT, reload=True, proxy_headers=True, forwarded_allow_ips=...)`.

Key options:

- **`reload=True`** — dev only. Prod uses systemd / Fly supervision; no reload.
- **`proxy_headers=True`** — honor `X-Forwarded-For` / `X-Forwarded-Proto` from the reverse proxy. Without this, the per-IP rate limiter would see the proxy's IP for every request and rate-limit all users together.
- **`forwarded_allow_ips`** — restricts who is trusted to set those headers. Default `127.0.0.1` (loopback only). Set to the proxy's IP if the proxy lives elsewhere.
- **`HOST` default `127.0.0.1`** — bind loopback only. `HOST=0.0.0.0` logs a warning because a directly-reachable app shouldn't trust proxy headers.

Prod deployments (Fly.io, self-hosted) bind to `127.0.0.1` and put Caddy / nginx / Fly's edge in front for TLS. See [deployment.md](deployment.md) for the runbook.
