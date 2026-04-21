# Auth

Open multi-user password auth. Register, log in, get a bearer token, send it as `Authorization: Bearer <token>` on every subsequent request. No cookies, no session middleware, no JWT. First user becomes admin; subsequent users are regular users. Optional invite-code gate closes registration.

This doc covers the password flow, token scheme, rate limiting, API-key encryption, and the OAuth migration path's shape. The deployment-side env vars (`SETTINGS_ENCRYPTION_KEY`, `ALLOWED_ORIGINS`, etc.) are covered in `CLAUDE.md`.

## File map

- `server/routes/auth.py` — HTTP endpoints.
- `auth/primitives.py` — `AuthenticatedUser`, `get_current_user` dependency, password hashing, token generation.
- `server/rate_limit.py` — sliding-window rate limiter keyed by IP.
- `auth/encryption.py` — Fernet wrapper for API-key storage.
- `storage/` — `users`, `auth_sessions`, `user_api_keys` tables (see [persistence.md](persistence.md#schema)).

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/auth/status` | Always safe; reports `{has_users, authenticated, user, invite_required}`. |
| `POST` | `/auth/register` | Open (or invite-code gated). First user → admin. Returns token. |
| `POST` | `/auth/login` | Email + password → token. Constant-time on unknown users. |
| `POST` | `/auth/logout` | Revokes the caller's bearer token. |
| `PUT` | `/auth/password` | Rotate password; invalidates all other sessions for this user. |
| `DELETE` | `/auth/me` | Cascade delete — user, sessions, API keys, conversations, exports, on-disk CSVs. |

All protected endpoints across the app depend on `get_current_user` (`auth/primitives.py:88`), which resolves the bearer token or raises 401. `/auth/status` uses the optional variant (`get_current_user_optional`, `auth/primitives.py:102`) so an unauthenticated caller gets a useful response instead of 401.

## The password flow

### Registration

`auth.py:141`. Steps:

1. Rate-limit check (`_register_limiter`: 5 attempts per 15 min per IP).
2. If `REGISTRATION_INVITE_CODE` env var is set, require a matching `invite_code` in the body. `secrets.compare_digest` avoids timing leaks on the code comparison.
3. Validate email shape (basic regex at `auth.py:47`) and normalize to lowercase.
4. `_create_user_from_verified_identity` — uniqueness check (409 on conflict), first-user → admin logic, insert row.
5. `_issue_session` — generate token, write `auth_sessions` row with `expires_at = now + AUTH_TOKEN_TTL_DAYS`.
6. Return `{token, user: {id, email, role}}`.

Pydantic (`RegisterRequest` in `server/schemas/`) enforces password minimum length before the handler runs.

### Login

`auth.py:169`. Steps:

1. Rate-limit check (`_login_limiter`: 10 attempts per 15 min per IP).
2. Lookup by lowercased email.
3. **Constant-time check even on unknown users.** If the user exists, verify against their hash; if not, verify against a dummy bcrypt hash (`auth.py:182`). Bcrypt's `checkpw` dominates the request latency either way, so an attacker can't time-probe whether an email is registered.
4. On mismatch or missing user → 401 with generic "Invalid email or password". Don't distinguish the two cases.
5. On success → issue a new session; return token + user.

Successful login doesn't revoke existing sessions. The user may have other devices/browsers signed in; login just adds one more token to the pile.

### Logout

`auth.py:193`. Parses the bearer token directly from the request header (`_extract_bearer` in `auth/primitives.py:58`) rather than through `get_current_user`, which only exposes the user record. Then `store.delete_auth_session(token)` removes the row. Subsequent requests with that token will 401.

`get_current_user` is still a dependency so an unauthenticated caller can't log anyone out — need a valid token first.

### Password change

`auth.py:207`. Requires the current password — without this check, a stolen bearer token could silently lock the owner out of all their other sessions. Flow:

1. Verify current password.
2. Update the hash.
3. `invalidate_other_auth_sessions(user_id, keep_token=current_token)` — deletes every session for this user except the calling one. The UI stays signed in; other devices need to re-authenticate.

### Delete account

`auth.py:239`. Requires the user's password. On success, `store.delete_user(user_id)` does a cascade:

- `sessions`, `tool_runs`, `assistant_parts`, `turns`, `compaction_summaries` linked to the user's conversations.
- `exports` rows — and the returned filenames are unlinked from disk in `EXPORTS_DIR`.
- `user_api_keys`, `auth_sessions`.
- The `users` row itself.

The calling session dies along with the rest, so the next request from the client will 401 and bounce to the auth screen.

## Password hashing

`auth/primitives.py:40`. bcrypt at default cost factor (12):

```python
def hash_password(plain): return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
def verify_password(plain, hashed): return bcrypt.checkpw(plain.encode(), hashed.encode())
```

Hash is stored in `users.password_hash` as a string. `verify_password` swallows `ValueError` on malformed hashes and returns `False` — belt-and-suspenders for corrupted rows.

Cost 12 → roughly ~300ms per verify on typical hardware. That's intentional: fast verifies enable credential stuffing; slow verifies don't. If the deployment hardware warrants bumping (or lowering) the cost, edit `hash_password` — new passwords pick up the new cost, old ones keep verifying at their original cost.

## Bearer tokens

`auth/primitives.py:53`. `secrets.token_urlsafe(32)` — 32 random bytes, base64-urlsafe encoded. ~256 bits of entropy. Collision-resistant; unguessable.

Stored as the primary key of `auth_sessions` alongside `user_id`, `expires_at`, `created_at`, `last_used_at`.

### Token resolution

`_resolve_user` (`auth/primitives.py:68`):

1. Pull bearer from `Authorization` (case-insensitive, handle both `Authorization` and `authorization`).
2. `store.get_auth_session(token)` — 401 if unknown.
3. Check expiry via lex-compare of ISO 8601 strings. Both sides are UTC same-format, so the string compare is correct.
4. Look up the user. If the user was deleted but the session wasn't (shouldn't happen given `ON DELETE CASCADE`, but belt-and-suspenders) → delete the orphan session and 401.
5. `touch_auth_session(token)` — refreshes `last_used_at`, but only when the stored timestamp is old enough to justify a write. This keeps authenticated read traffic from writing on every request while preserving recent-activity telemetry.

### TTL

Default 30 days (`AUTH_TOKEN_TTL_DAYS` env var). The lifespan purges expired sessions on startup (`app.py:49`); the resolver cleans them up lazily on access. There's no background sweeper — the two together are enough.

## Rate limiting

`server/rate_limit.py`. In-memory sliding-window limiter, keyed by client IP.

Three limiters on the auth router:

| Limiter | Max | Window | Protects |
|---------|-----|--------|----------|
| `_register_limiter` | 5 | 15 min | Registration spam / invite-code brute force |
| `_login_limiter` | 10 | 15 min | Credential stuffing |
| `_account_limiter` | 20 | 15 min | Password change / account delete (defense in depth; caller already holds a valid token) |

IP is derived from `request.client.host` (`rate_limit.py:21`). When deployed behind a reverse proxy, uvicorn's `proxy_headers=True` + `forwarded_allow_ips` populates `request.client.host` from `X-Forwarded-For` (see [transport.md](transport.md#running-the-server)). Without that, all traffic looks like it comes from the proxy IP and gets rate-limited as one.

On limit: `HTTPException(429)` with a `Retry-After` header.

**Single-process only.** The limiter stores attempts in a local dict. Multi-worker deployments would see attempts spread across workers and effectively multiply the quota. A comment in the file flags this (`rate_limit.py:6`) — swap for slowapi or a Redis-backed limiter if scale-out is ever needed.

## API keys

Users can bring their own Anthropic / OpenAI keys via the Settings modal. Storage is Fernet-encrypted.

### Encryption

`auth/encryption.py`. Uses `cryptography.fernet.Fernet` — AES-128-CBC + HMAC-SHA256 with a master key from `SETTINGS_ENCRYPTION_KEY` env var.

- `encrypt(plaintext) -> str` — ciphertext as base64 URL-safe string.
- `decrypt(ciphertext) -> str` — raises `ValueError` on auth failure (wrong key, tampered ciphertext).
- `require_configured()` — lifespan calls this at startup; fails fast with a generate-me hint if the key is missing or malformed.

The Fernet instance is lazily loaded (`encryption.py:42`) so CLI contexts without the env var don't blow up on import.

### Storage

`user_api_keys` table (see [persistence.md](persistence.md#auth-data)). Composite PK `(user_id, provider)`. `encrypted_key` column holds the Fernet ciphertext as a string. `ON DELETE CASCADE` from users.

### Retrieval

Flow at request time:

1. `resolve_user_credential` in `server/dependencies.py` calls `store.get_api_key(user_id, provider)`.
2. If a row exists, `encryption.decrypt(rec.encrypted_key)`. On `ValueError` (tampered / key-era mismatch), log and return `None`.
3. The plain key is passed to `create_client_for_request` as `api_key=...`, which short-circuits the env-var lookup.
4. For OAuth providers (`credential_shape="codex_oauth"`) the decrypted payload is a JSON bundle; `resolve_user_credential` refreshes the access token when near expiry, re-encrypts, and upserts before returning the bearer string. See `auth/codex_oauth.py` and `server/routes/codex_oauth.py` for the device-code flow.

### Key rotation pitfall

Rotating `SETTINGS_ENCRYPTION_KEY` without a re-encrypt step bricks every stored key — ciphertext encrypted under the old key can't decrypt with the new one. The user's chat requests would see "no API key" until they re-enter their keys. Flagged in `CLAUDE.md`.

The `decrypt` error path deliberately treats this as "no key" rather than raising: the user gets a clean "add one in Settings" prompt instead of a 500 on their next chat request.

## First-user admin & orphan backfill

`_create_user_from_verified_identity` (`auth.py:79`):

```python
is_first_user = store.count_users() == 0
role = "admin" if is_first_user else "user"
```

Plus, if this is the first user, `store.backfill_orphan_ownership(user.id)` runs — sweeping any pre-existing sessions or exports with `NULL user_id` to belong to the new admin. This covers the single-user → multi-user migration: someone who used the app before auth existed keeps their history when they register.

There's no way to promote other users to admin via the UI. To make another user admin, do it directly in SQLite. The admin role doesn't currently gate much — the multi-user story is mostly per-user isolation, not admin tooling.

`ensure_admin_exists` in the lifespan (`app.py:60`) is a safety net: if for some reason the `role` column is empty across all users (e.g., migrating a pre-role DB), it promotes the oldest user to admin so the deployment has at least one.

## `AuthenticatedUser` vs `UserRecord`

`AuthenticatedUser` (`auth/primitives.py:28`) is a lightweight view of the current user that routers depend on: `id`, `email`, `role`. `UserRecord` (the full SQLite row) carries `password_hash` — which routers shouldn't accidentally serialize.

The dependency returns `AuthenticatedUser`; if a router needs the password_hash (password change, account delete), it explicitly calls `store.get_user_by_id(user.id)` to get the `UserRecord`. This is a small but important firewall: no way to leak `password_hash` through `AuthUser` in a response body.

## Why not JWT?

Three reasons:

1. **Revocable logout.** Revoking a JWT requires a denylist, which defeats the stateless benefit and reintroduces the server-side lookup we'd be using anyway.
2. **Password changes invalidate other sessions.** Same problem — would need a denylist.
3. **Stored tokens are simpler.** `auth_sessions` is a 5-column table and `get_auth_session(token)` is one indexed query; the added cost over JWT verification is one SQLite roundtrip, well under the cost of a single chat request.

If the app ever needs true horizontal scale, moving to JWT + a denylist cache (Redis) is an option, but the current shape is right-sized for personal / small-team deployments.

## OAuth migration path (planned, not shipped)

`CLAUDE.md` has the full plan. The code is deliberately shaped to slot OAuth in without touching the password path:

- `_create_user_from_verified_identity` (`auth.py:79`) takes an already-verified identity (email + password_hash + verified flag). The OAuth callback will call this with `password_hash=None` and `verified=True`.
- `_issue_session` (`auth.py:119`) is the same for both paths — generate a token, write the session row.

The new pieces OAuth will bring:

- `user_identities` table linking `(user_id, provider, provider_subject)`, seeded with `('password', email)` rows for existing users.
- `/auth/oauth/google/start` + `/auth/oauth/google/callback` endpoints doing the PKCE dance.
- A Google button in the UI's reserved `.auth-alt` slot.
- A settings-modal "Linked Accounts" section to unlink identities.

Not done yet. When it is, the six-step checklist in `CLAUDE.md` is the landing zone.
