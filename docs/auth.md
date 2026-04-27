# Auth

Open multi-user authentication. Register with a password, sign in with Google, or sign in with ChatGPT (Codex device flow). Issued credential is an opaque session token, then authenticate via the browser session cookie or `Authorization: Bearer <token>`. No JWT. First user becomes admin; subsequent users are regular users. Optional invite-code gate closes password registration.

This doc covers the password flow, token scheme, rate limiting, browser session cookie + CSRF protection, Fernet-encrypted per-user API keys, the Codex OAuth device-code flow (for ChatGPT users — both as a credential for chat and as a sign-in method), and Google OAuth sign-in/linking. Deployment-side env vars (`SETTINGS_ENCRYPTION_KEY`, `ALLOWED_ORIGINS`, `GOOGLE_OAUTH_CLIENT_ID`, etc.) are in [deployment.md](deployment.md).

## File map

**Primitives (`backend/domain/auth/`):**
- `backend/domain/auth/primitives.py` — password hashing (bcrypt) and opaque-token generation.
- `backend/domain/auth/types.py` — `AuthenticatedUser`, OAuth token bundles, identity-provider constants.
- `backend/domain/auth/encryption.py` — Fernet wrapper (`encrypt` / `decrypt`) + `require_configured()` startup check.
- `backend/domain/auth/codex_oauth.py` — ChatGPT device-code OAuth protocol (`request_device_code`, `poll_device_code`, `exchange_code`, `refresh_access_token`). Talks directly to `https://auth.openai.com`.
- `backend/domain/auth/audit.py` — shared `AuditContext` and `audit_log` helper used by auth and OAuth services.

**Services (`backend/application/`):**
- `auth.py` — `AuthService` (register, login, logout, change_password, delete_account) plus the service's errors and `RegistrationResult`.
- `oauth/provider_credentials.py` — `ProviderCredentialService.get_api_key` dispatches `api_key` vs `codex_oauth` shapes.
- `oauth/codex.py` — `CodexOAuthService` device flow + `resolve_access_token` credential resolver.
- `oauth/google.py` — Google sign-in / sign-up / link / unlink flow + the flow's errors and DTOs.
- `settings.py` — `SettingsService`: per-provider key status, upsert/delete.
- `providers.py` — `ProviderService`: provider availability using server config plus user keys.

**Wire DTOs (`backend/api/schemas/`):**
- `auth.py` — `RegisterRequest`, `LoginRequest`, `AuthTokenResponse`, etc.
- `settings.py`, `oauth_codex.py`, etc. — one file per feature.

**Shared lifecycle:**
- `backend/domain/auth/lifecycle.py` — `create_user_account` + `issue_session`, used by password auth and OAuth callbacks alike.

**Routes (`backend/api/routes/`):**
- `auth.py` — `/auth/*` HTTP endpoints.
- `settings.py` — `/settings/api-keys`, `/settings/oauth/codex/*`, and linked-identity endpoints.

**Storage:**
- `backend/data/repositories/users/` — `UsersMixin` + `UserIdentitiesMixin` + `LoginFailuresMixin` + `EmailVerificationMixin` + `SecurityEventsMixin` (one file per mixin): CRUD for `users`, `auth_sessions`, `user_api_keys`, `user_identities`, `login_failures`, `email_verification`, `security_events`.
- `backend/server/rate_limit.py` — sliding-window IP rate limiter.
- `backend/server/session.py` — FastAPI request parsing for Bearer tokens plus browser session/CSRF cookie helpers.

## Endpoints

| Method | Path | Auth | Rate limit | Notes |
|--------|------|------|-----------|-------|
| `GET` | `/auth/status` | Optional | — | `{has_users, authenticated, user, invite_required}`. |
| `POST` | `/auth/register` | No | 5 / 15 min / IP | First user → admin. Returns `{token, user}`. |
| `POST` | `/auth/login` | No | 10 / 15 min / IP | Constant-time on unknown users. |
| `POST` | `/auth/logout` | Yes | — | Revokes the caller's bearer token. |
| `PUT` | `/auth/password` | Yes | 20 / 15 min / IP | Verifies current password; invalidates sibling sessions. |
| `DELETE` | `/auth/me` | Yes | 20 / 15 min / IP | Cascade delete — user, sessions, keys, conversations, exports (incl. unlinking CSVs on disk). |
| `GET` | `/settings/api-keys` | Yes | — | Per-provider `ApiKeyStatus` (has_key; for OAuth: email + expires_at). |
| `PUT` | `/settings/api-keys/{provider}` | Yes | — | Set / clear an API key. Refuses a raw Codex OAuth key (use the device flow). |
| `POST` | `/settings/oauth/codex/start` | Yes | 5 / 60 min / IP | Starts ChatGPT device-code flow from Settings; returns `user_code` + `verification_url`. |
| `GET` | `/settings/oauth/codex/status` | Yes | — | Polls `pending` / `complete` / `expired` / `error` for an in-flight flow. |
| `DELETE` | `/settings/oauth/codex/cancel` | Yes | — | Cancels the background polling task. |
| `POST` | `/auth/oauth/openai/start` | No | 5 / 60 min / IP | Sign-in with ChatGPT — same device-code flow, but invoked from the unauthenticated sign-in screen. On success, creates a new account or signs in the existing one. |
| `GET` | `/auth/oauth/openai/status` | No | — | Status poll for the sign-in flow. Issues a session on `complete`. |
| `DELETE` | `/auth/oauth/openai/cancel` | No | — | Cancels the sign-in flow. |
| `GET` | `/auth/oauth/google/start` | No | — | Begins Google OAuth (PKCE + state + nonce); 302s to Google. |
| `GET` | `/auth/oauth/google/callback` | No | — | Handles Google's redirect; verifies state and ID token; issues session (sign-in flow) or attaches identity (link flow). |
| `GET` | `/settings/identities` | Yes | — | Lists the user's linked auth identities. |
| `DELETE` | `/settings/identities/{provider}` | Yes | — | Unlinks an identity. Refused when it would leave the user with no way to sign in. |

Routes are thin translators: parse the request, call the service, translate service exceptions to HTTP. The actual business logic lives in `backend/application/`; see [transport.md](transport.md#services-layer).

Protected endpoints depend on `get_current_user` (`backend/api/dependencies.py:59`) which resolves the bearer token or raises 401. `/auth/status` uses `get_current_user_optional` so an unauthenticated caller still gets a useful response.

## The password flow

All password endpoints delegate to `AuthService` in `backend/application/auth.py`. Routes pass the user-provided body + (if authenticated) the current user.

### Registration — `AuthService.register` (`auth.py`)

1. Rate-limit check (registered in the route).
2. If `REGISTRATION_INVITE_CODE` is set, require a matching `invite_code` in the body. `secrets.compare_digest` avoids timing leaks on the code comparison.
3. Normalize email (lowercase, strip).
4. `create_user_account` (`backend/domain/auth/lifecycle.py`) — uniqueness check (409 on conflict), first-user → admin logic, insert row, optionally seed a `password` identity row. If it's the first user, `store.backfill_orphan_ownership(user.id)` sweeps any `NULL user_id` rows to the new admin (single-tenant → multi-user migration).
5. `issue_session` (`backend/domain/auth/lifecycle.py`) — generate a token, write `auth_sessions` with `expires_at = now + AUTH_TOKEN_TTL_DAYS`.
6. Return `{token, user: {id, email, role}}`.

Pydantic (`RegisterRequest` in `backend/api/schemas/auth.py`) enforces password minimum length before the handler runs.

### Login — `AuthService.login` (`auth.py`)

1. Rate-limit check (route).
2. Lookup by lowercased email.
3. **Constant-time check even on unknown users** — if the user exists, verify against their hash; if not, verify against a dummy bcrypt hash (`auth.py`). Bcrypt's `checkpw` dominates latency either way, so an attacker can't time-probe whether an email is registered.
4. On mismatch or missing user → 401 "Invalid email or password". Don't distinguish.
5. On success → issue a new session; return `{token, user}`.

Successful login doesn't revoke existing sessions. The user may have other devices/browsers signed in; login just adds one more token to the pile.

### Logout — `AuthService.logout` (`auth.py`)

The route parses the current session token directly via `_extract_session_token` rather than through `get_current_user`, then calls `store.delete_auth_session(token)`. Subsequent requests with that token will 401. `get_current_user` is still a dependency so an unauthenticated caller can't log anyone out — need a valid token first.

### Password change — `AuthService.change_password` (`auth.py`)

Requires the current password — without this check, a stolen bearer token could silently lock the owner out of all their other sessions. Flow:

1. Verify current password.
2. Update the hash (new bcrypt cost picks up any config change).
3. `invalidate_other_auth_sessions(user_id, keep_token=current_token)` deletes every session for this user except the calling one. The UI stays signed in; other devices need to re-authenticate.

### Delete account — `AuthService.delete_account` (`auth.py`)

Requires the user's password. On success, `store.delete_user(user_id)` cascades:

- `sessions`, `tool_runs`, `assistant_parts`, `turns`, `compaction_summaries` linked to the user's conversations.
- `exports` rows — and the returned filenames are unlinked from disk in `EXPORTS_DIR`.
- `user_api_keys`, `auth_sessions`.
- The `users` row itself.

The calling session dies along with the rest, so the next request from the client will 401 and bounce to the auth screen.

## Password hashing

`backend/domain/auth/primitives.py:38`. bcrypt at default cost factor (12):

```python
def hash_password(plain): return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
def verify_password(plain, hashed): return bcrypt.checkpw(plain.encode(), hashed.encode())
```

Hash is stored in `users.password_hash` as a string. `verify_password` swallows `ValueError` on malformed hashes and returns `False` — belt-and-suspenders for corrupted rows.

Cost 12 → roughly ~300ms per verify on typical hardware. Intentional: fast verifies enable credential stuffing; slow verifies don't. If the deployment hardware warrants bumping (or lowering) the cost, edit `hash_password` — new passwords pick up the new cost, old ones keep verifying at their original cost.

## Bearer tokens

`backend/domain/auth/primitives.py`. `secrets.token_urlsafe(32)` — 32 random bytes, base64-urlsafe encoded. ~256 bits of entropy. Collision-resistant; unguessable.

Stored as the primary key of `auth_sessions` alongside `user_id`, `expires_at`, `created_at`, `last_used_at`.

### Token resolution

`_resolve_user` (`backend/api/dependencies.py:37`):

1. `_extract_session_token(request)` (`backend/server/session.py`) — parse `Authorization` / `authorization` Bearer first, then fall back to the `session` cookie.
2. `store.get_auth_session(token)` — 401 if unknown.
3. Check expiry via lex-compare of ISO 8601 strings. Both sides are UTC same-format, so string compare is correct.
4. Look up the user. If the user was deleted but the session wasn't (shouldn't happen given `ON DELETE CASCADE`, but belt-and-suspenders) → delete the orphan session and 401.
5. `touch_auth_session(token, min_interval_seconds=AUTH_SESSION_TOUCH_INTERVAL_SECONDS)` — refreshes `last_used_at`, but only when the stored timestamp is old enough (default: 5 minutes) to justify a write. Keeps authenticated read traffic from writing on every request while preserving recent-activity telemetry.

### TTL

Default 30 days (`AUTH_TOKEN_TTL_DAYS` env var). The lifespan purges expired sessions on startup (via `run_housekeeping` in `backend/server/startup.py`); the resolver cleans them up lazily on access. No background sweeper — the two together are enough.

### Browser session cookie + CSRF

The browser UI doesn't store the bearer token in JS — it rides an `HttpOnly`, `Secure`, `SameSite=Lax` cookie named `session`. `Authorization: Bearer …` is still accepted (API clients, the `/docs` tester) but the React app never sends it.

Because cookies are auto-attached cross-origin, the cookie path needs CSRF protection. The implementation (`backend/server/csrf.py`, attached as a dependency by every router that mutates state) is **double-submit**:

1. On any successful auth response (login, register, OAuth callback), the server sets a JS-readable `csrf_token` cookie alongside the `session` cookie.
2. The browser reads `csrf_token` from `document.cookie` on each mutating request and echoes it in the `X-CSRF-Token` header.
3. `verify_csrf` (the dependency) compares the cookie against the header and rejects mismatches with 403. Bearer-token requests (no cookie) skip the check — browsers can't auto-attach `Authorization` cross-origin, so CSRF doesn't apply.

Safe-method requests (GET / HEAD / OPTIONS) skip the check by convention; mutating routes register the dependency explicitly.

## Rate limiting

`backend/server/rate_limit.py`. In-memory sliding-window limiter, keyed by client IP. Four limiters live on `AppProcessState` (`backend/server/process_state.py`):

| Limiter | Max | Window | Protects |
|---------|-----|--------|----------|
| `register_limiter` | 5 | 15 min | Registration spam / invite-code brute force |
| `login_limiter` | 10 | 15 min | Credential stuffing |
| `account_limiter` | 20 | 15 min | Password change / delete account (defense in depth; caller already holds a valid token) |
| `codex_start_limiter` | 5 | 60 min | ChatGPT device-flow start (external-endpoint amplification) |

IP is derived from `request.client.host`. When deployed behind a reverse proxy, uvicorn's `proxy_headers=True` + `forwarded_allow_ips` populates it from `X-Forwarded-For` (see [transport.md](transport.md#running-the-server)). Without that, all traffic looks like it comes from the proxy IP and gets rate-limited as one.

On limit: `HTTPException(429)` with a `Retry-After` header.

**Single-process only.** The limiter stores attempts in a local dict. Multi-worker deployments would see attempts spread across workers and effectively multiply the quota. Swap for slowapi or a Redis-backed limiter if scale-out is ever needed.

## API keys

Users can bring their own Anthropic / OpenAI keys via the Settings modal. Storage is Fernet-encrypted. Codex (ChatGPT) credentials live in the same `user_api_keys` row but are a JSON OAuth bundle rather than a raw API key — see [Codex OAuth flow](#codex-oauth-flow).

### Encryption

`backend/domain/auth/encryption.py`. Uses `cryptography.fernet.Fernet` — AES-128-CBC + HMAC-SHA256 with a master key from `SETTINGS_ENCRYPTION_KEY`.

- `encrypt(plaintext) -> str` — ciphertext as base64 URL-safe string.
- `decrypt(ciphertext) -> str` — raises `ValueError` on auth failure (wrong key, tampered ciphertext).
- `require_configured()` — called in the lifespan (via `validate_encryption()` in `backend/server/startup.py`); fails fast with a generate-me hint if the key is missing or malformed.

The Fernet instance is lazily loaded so import-only contexts without the env var don't blow up on import.

### Storage

`user_api_keys` table (see [persistence.md](persistence.md#auth-data)). Composite PK `(user_id, provider)`. `encrypted_key` column holds the Fernet ciphertext as a string. `ON DELETE CASCADE` from users.

### Retrieval

Flow at request time, inside `ChatService.prepare_chat`:

1. `ProviderCredentialService.get_api_key(user_id, provider_name)` (`oauth/credentials.py`) dispatches on `provider_info.credential_shape`.
2. **`api_key`** shape: `store.get_api_key(user_id, provider)`; if a row exists, `encryption.decrypt(rec.encrypted_key)`. On `ValueError` (tampered / key-era mismatch), log and return `None`.
3. **`codex_oauth`** shape: `codex_credentials.resolve_access_token(...)` — see below.
4. The resolved bearer is passed to `create_client_for_request(provider, model, api_key=user_key)`. If the user has no key and no env var fallback exists, the route surfaces `ChatConfigurationError → 503` ("add one in Settings").

### Key rotation pitfall

Rotating `SETTINGS_ENCRYPTION_KEY` without a re-encrypt step bricks every stored key — ciphertext encrypted under the old key can't decrypt with the new one. The user's chat requests would see "no API key" until they re-enter theirs. Flagged in `CLAUDE.md`.

The `decrypt` error path deliberately treats this as "no key" rather than raising — the user gets a clean "add one in Settings" prompt instead of a 500 on their next chat request.

## Codex OAuth flow

Some users don't have an OpenAI API key but do have a ChatGPT subscription. OpenAI's Codex CLI exposes a public client ID (`CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"`, `backend/domain/auth/codex_oauth.py:28`) that lets a third-party app stand up the same device-code flow Codex uses, talking to `https://auth.openai.com`.

The same flow drives **two surfaces**: a *credential* path (already-authenticated user from Settings → Connect ChatGPT, providing a chat credential) and a *sign-in* path (unauthenticated user clicking "Sign in with ChatGPT" on the auth screen, which seeds an account on completion). Both pass through `CodexOAuthService` and `run_device_flow`; the difference is the route that starts them and what happens when the bundle returns:

- *Credential path* (`/settings/oauth/codex/*`): on success, the encrypted bundle goes into `user_api_keys` for the current user.
- *Sign-in path* (`/auth/oauth/openai/*`): on success, the email from the JWT is matched against existing users — found → sign in; not found → `create_user_account` with the OAuth password sentinel, seeding a `('openai-codex', <sub>)` identity row. Either way, `issue_session` sets the session + csrf cookies and the next page load is signed in.

### Protocol (`backend/domain/auth/codex_oauth.py`)

1. **`request_device_code(client_id)`** (`backend/domain/auth/codex_oauth.py:141`) → `POST https://auth.openai.com/api/accounts/deviceauth/usercode` → returns `DeviceCodeStart(device_auth_id, user_code, interval, verification_url)`. The user code is what the user types at `https://auth.openai.com/codex/device`.
2. **`poll_device_code(device_auth_id, user_code, interval_seconds=5, max_wait_seconds=15*60)`** (`backend/domain/auth/codex_oauth.py:181`) → polls `POST /api/accounts/deviceauth/token` every 5s for up to 15 minutes. Treats 403/404 as "still waiting"; raises `DeviceCodeExpired` on timeout.
3. **`exchange_code(auth_code, code_verifier)`** (`backend/domain/auth/codex_oauth.py:232`) → `POST https://auth.openai.com/oauth/token` → returns `TokenBundle(access_token, refresh_token, expires_at, email)`. `expires_at` is parsed from the JWT's `exp` claim (or `expires_in + 55 minutes` fallback).
4. **`refresh_access_token(refresh_token)`** (`backend/domain/auth/codex_oauth.py:252`) → same endpoint, `grant_type=refresh_token` → returns a new bundle. **OpenAI may rotate the refresh token**, so the caller must replace the stored bundle with the return value.

### Transport-side orchestration (`oauth/codex.py`)

- **`start(user_id)`** (`oauth/codex.py`) — evicts stale flows, calls `request_device_code`, registers a `PendingCodexOAuthFlow` in `PendingCodexOAuthFlows`, and spawns `run_device_flow` as an `asyncio.Task`. Returns `{pending_id, user_code, verification_url, expires_in: 900}` to the browser.
- **`run_device_flow(pending_id)`** — polls for completion; on success, calls `exchange_code`, encrypts the JSON bundle, and `store.upsert_api_key(user_id, provider="openai-codex", encrypted_key=...)`. Sets flow status to `complete`. On timeout or error, sets `expired` / `error`.
- **`status(pending_id, user_id)`** — IDOR-checked lookup. Pops the flow once it reaches a terminal state.
- **`cancel(pending_id, user_id)`** — cancels the asyncio task.

On worker shutdown, `AppProcessState.aclose()` cancels every in-flight flow task so they don't leak past the lifespan.

### Access-token refresh (`oauth/codex.py`)

Once connected, the access token is short-lived (Codex returns ~1-hour tokens). Refresh happens on-demand, not on a schedule:

- `resolve_access_token(store, user_id, provider_name, refresh_locks)` (`oauth/codex.py`):
  1. Load + decrypt the stored `TokenBundle`.
  2. If `not is_near_expiry(bundle, skew=REFRESH_SKEW_SECONDS=30)`, return the current access token.
  3. Otherwise acquire the per-user refresh lock (`refresh_locks.for_user(user_id)` in `InMemoryPerUserLockRegistry`).
  4. Double-check expiry inside the lock (another request may have just refreshed).
  5. If still near expiry, call `codex_oauth.refresh_access_token(bundle.refresh_token)`, re-encrypt, `store.upsert_api_key`, release lock, return the new access token.

The per-user lock serializes refreshes when multiple concurrent requests all hit a near-expiry token at once — otherwise each would call `/oauth/token` and one of them would get rate-limited. `PerUserLockRegistry` sits on `AppProcessState` so all requests within a worker share the same lock map.

### Settings integration

`SettingsService.list_api_key_status` (`settings.py`) surfaces Codex differently from API-key providers: for a `codex_oauth` row it decrypts the bundle and returns `email` + `expires_at` alongside `has_key=true`. The UI renders "Connected as <email>, expires <date>" and a Reconnect button.

`SettingsService.update_api_key` refuses a non-null body for `openai-codex` — the only way to create a Codex credential is the device flow. This prevents users from pasting a raw access token that would bypass the refresh-lock logic.

## First-user admin & orphan backfill

`create_user_account` (`backend/domain/auth/lifecycle.py`):

```python
is_first_user = await store.count_users() == 0
role = "admin" if is_first_user else "user"
```

If this is the first user, `store.backfill_orphan_ownership(user.id)` runs — sweeping any pre-existing sessions or exports with `NULL user_id` to belong to the new admin. This covers the single-user → multi-user migration: someone who used the app before auth existed keeps their history when they register.

There's no way to promote other users to admin via the UI. To make another user admin, do it directly in SQLite. The admin role doesn't currently gate much — the multi-user story is mostly per-user isolation, not admin tooling.

`ensure_admin_exists`, called in the lifespan via `run_housekeeping` (`backend/server/startup.py`), is a safety net: if the `role` column somehow ends up empty across all users (e.g., migrating a pre-role DB), it promotes the oldest user to admin so the deployment has at least one.

## `AuthenticatedUser` vs `UserRecord`

`AuthenticatedUser` (`backend/domain/auth/types.py`) is a lightweight view of the current user that routes + services depend on: `id`, `email`, `role`. `UserRecord` (the full SQLite row) carries `password_hash` — which should never accidentally serialize.

The dependency returns `AuthenticatedUser`; if a service method needs the `password_hash` (password change, account delete), it explicitly calls `store.get_user_by_id(user.id)` to get the `UserRecord`. This is a small but important firewall: no way to leak `password_hash` through `AuthenticatedUser` in a response body.

## Why not JWT

Three reasons:

1. **Revocable logout.** Revoking a JWT requires a denylist, which defeats the stateless benefit and reintroduces the server-side lookup we'd be avoiding.
2. **Password changes invalidate other sessions.** Same problem — would need a denylist.
3. **Stored tokens are simpler.** `auth_sessions` is a 5-column table; `get_auth_session(token)` is one indexed query. The added cost over JWT verification is one SQLite roundtrip, well under the cost of a single chat request.

If the app ever needs true horizontal scale, moving to JWT + a denylist cache (Redis) is an option, but the current shape is right-sized for personal / small-team deployments.

## Google OAuth sign-in

Live since 2026-04-23. Users can sign up / sign in with Google, and existing password users can link their Google account from Settings → Account. The shared auth lifecycle helpers keep Google account creation aligned with password registration:

- `create_user_account` (`backend/domain/auth/lifecycle.py`) takes an already-verified identity (`IdentitySeed` with the OAuth password sentinel and `verified=True`). Both the password registration path and the OAuth callback go through it.
- `issue_session` is the same for both paths — sets the same `session` + `csrf_token` cookies and stores the row in `auth_sessions`.

The Google-specific pieces:

- `user_identities` table linking `(user_id, provider, provider_subject)`. A user who registered with a password has one `('password', email)` row; signing in with Google later adds a second `('google', <google_sub>)` row (auto-link by verified email). Deleting the user cascades.
- OAuth-only users (no password) get `users.password_hash = "!"` — a sentinel that bcrypt rejects, so `verify_password` is False for any attempt. Avoids a NOT NULL schema rebuild and keeps the password verification path uniform.
- `/auth/oauth/google/start` (generates PKCE + state + nonce, 302s to Google) + `/auth/oauth/google/callback` (verifies state, exchanges code, verifies the ID token against Google's JWKS — cached 1h).
- A "Continue with Google" button on the sign-in screen, gated on whether `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` are set.
- Settings → **Account** lists every linked identity (password, google, openai-codex) and lets the user unlink any *non-final* one. Unlinking is refused if it would leave the user with no way to sign in (e.g. an OAuth-only user can't unlink their last identity).

The OAuth `state` parameter is the anti-CSRF for the callback (it's a GET, so the usual `X-CSRF-Token` header check doesn't apply); session cookies still travel on the callback, which is how the server distinguishes a *link flow* (authenticated user already attached) from a *sign-in flow* (no current session).

Follow-up work: OAuth-only users currently can't set a password. Adding `POST /auth/set-password` (authenticated session, rejects if `password_hash != "!"`, writes a real bcrypt hash, seeds a `password` identity row) would let an OAuth user become a hybrid password+Google user. Not blocking — users can keep using OAuth indefinitely, or unlink Google if another identity exists.
