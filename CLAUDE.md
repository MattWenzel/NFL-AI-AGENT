# nflverse DB

NFL player stats database built from [nflverse](https://github.com/nflverse/nflverse-data) data.

## Quick Reference

| Database | Size | Tables | Rows | Years |
|----------|------|--------|------|-------|
| `nflverse.duckdb` | ~1.2 GB | 25 + 1 view | ~5M | 1999-2025 |

Single DuckDB file with **78 FK constraints** enforced at build time. Accessed read-only by the chat agent via `core/tools/sandbox/runner.py` (raw `duckdb.connect(..., read_only=True)`); no ORM involvement.

**Full schema**: `../NFLVERSE/docs/CONSUMER_GUIDE.md` (short, gotcha-focused) + `../NFLVERSE/docs/DATABASE.md` (full reference). Sibling repo.

## Key Tables

**Player reference**: `players`, `player_ids`

**Games / teams / venues**: `games`, `stadiums`, `officials` (joins via `old_game_id`), `team_game_stats`, `team_season_stats`

**Player stats (weekly + season)**: `game_stats`, `season_stats` (REG + POST), `weekly_rosters` (2002+), `snap_counts` (2015+), `ngs_stats` (2016+), `pfr_advanced` (2018+, now includes defense), `pfr_advanced_weekly` (2018+), `qbr` (2006-2025), `injuries` (2009+)

**Player meta / contracts**: `draft_picks` (1980+), `combine` (2000+), `contracts` (apy in millions of dollars), `contracts_cap_breakdown` (year-by-year cap)

**Depth charts**: `v_depth_charts` (preferred — UNION view). Base tables `depth_charts` (2001-2024), `depth_charts_2025` (daily grain).

**Play-by-play**: `play_by_play` (1.28M plays, 372 cols), `pbp_participation` (2016+), `ftn_charting` (2022+).

## ID System

Every player-bearing table carries **`player_gsis_id`** as the canonical join key — use it for every player join. Source-native IDs (`player_pfr_id`, `player_espn_id`) are also present on `players` if needed. `player_ids` is the cross-reference for non-canonical IDs (yahoo, sleeper, fantasy_data, pff).

Pre-GSIS historical players use Elias-format IDs (`VIT276861`, `YOU597411`) — deliberate, so draft_picks and HoF queries still join. Don't filter `LIKE '00-%'`.

## API

Base URL: `http://localhost:8001` | Interactive docs: `/docs` (OpenAPI) | Read-only database.

Key access patterns:

- **`POST /chat/stream`** — AI chat with streaming
- **`GET /exports/{filename}`** — CSV export download

## AI Chat Agent

Natural language interface to the database. Supports Anthropic Claude and OpenAI GPT with tool_use to translate questions into SQL queries.

### LLM Providers

| Provider | Auth | Default Model | Context |
|----------|------|---------------|---------|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | 200K |
| OpenAI | `OPENAI_API_KEY` | `gpt-5` | 128K |

Select via `CHAT_PROVIDER` env var (default: `anthropic`) or the UI dropdown.

The chat runtime is transcript-backed: sessions, turns, assistant parts, tool runs, and compaction summaries are persisted in `data/runtime.sqlite3`. Long conversations are compacted by summarizing older turns and excluding older raw tool output from active prompt context while keeping the full transcript in storage.

### Architecture

Hexagonal split: `core/` holds transport-agnostic libraries (none import FastAPI), `app/` is the FastAPI HTTP application. Inside `app/processes/`, each HTTP business process is a self-contained folder with its own routes + service + DTOs + errors.

```
app/                              # FastAPI HTTP application
├── main.py                       #   app factory + lifespan, run.py entry
├── bootstrap/                    #   app construction + cross-cutting concerns
│   ├── startup.py                #     DB validation, runtime wiring, housekeeping
│   ├── logging.py                #     setup_logging + secret-redacting filter
│   ├── middleware.py             #     SecurityHeadersMiddleware (CSP / HSTS / etc.)
│   ├── csrf.py                   #     double-submit CSRF dep + cookie helpers
│   ├── audit.py                  #     audit_log helper (security_events row + structured log)
│   ├── dependencies.py           #     FastAPI Depends factories (get_store, get_runtime, ...)
│   ├── process_state.py          #     per-process state (lock registries, pending OAuth flows, AppProcessState)
│   └── rate_limit.py             #     per-IP RateLimiter + concurrency limiter
└── processes/                    #   one folder per HTTP business process
    ├── auth/                     #     register / login / logout / password / delete / verify / resend
    ├── chat/                     #     POST /chat/message, POST /chat/stream + SSE event serializer
    ├── conversations/            #     list / transcript / patch / delete
    ├── exports/                  #     CSV library CRUD + GET /exports/{filename} download
    ├── oauth/
    │   ├── codex/                #       ChatGPT device-code OAuth
    │   ├── google/               #       Google OAuth + identity link/unlink
    │   ├── credentials.py        #       cross-flow credential lookup
    │   └── errors.py             #       cross-flow OAuth errors
    ├── providers/                #     GET /chat/providers
    └── settings/                 #     per-user API key CRUD + linked-identity list

# Each app/processes/<feature>/ contains:
#   routes.py    HTTP handlers
#   service.py   orchestration class (depends on core/)
#   schemas.py   Pydantic wire-format models
#   types.py     internal DTOs (PreparedChat, AuditContext, IssuedSession, ...)
#   errors.py    feature-specific exceptions

core/                             # Transport-agnostic libraries — no FastAPI imports
├── config.py                     #   DB paths, env loading, runtime settings
├── agent/                        # Chat runtime loop
│   ├── runtime.py                #     ChatRuntime: prepare_session, run_session
│   ├── turn.py                   #     Turn: per-user-message state machine + tool execution + doom-loop detection
│   ├── events.py                 #     11 typed RuntimeEvent variants (TurnStartedEvent, ToolPendingEvent, ...)
│   ├── errors.py                 #     RuntimeLoopError
│   ├── types.py                  #     ToolExecutor Protocol, ToolExecutionResult
│   ├── message_builder.py        #     transcript → wire-format message list
│   ├── system_prompt.py          #     base prompt template (~2.3K tokens)
│   └── compaction/               #     transcript shrinking
│       ├── policy.py             #       selector + RetentionPolicy + compact_if_needed
│       ├── summarizer.py         #       LLM-backed summarization
│       └── token_counting.py     #       tiktoken cl100k estimator
├── auth/                         # Auth primitives + OAuth wire helpers
│   ├── primitives.py             #     bcrypt, tokens, cookies, session-token extraction
│   ├── encryption.py             #     Fernet wrapper for at-rest secrets
│   ├── email.py                  #     Resend wrapper (verification email)
│   ├── codex_oauth.py            #     Codex device-code OAuth functions
│   ├── google_oauth.py           #     Google OAuth + JWKS verification
│   ├── errors.py                 #     CodexOAuthError, GoogleOAuthError, encryption errors, ...
│   └── types.py                  #     AuthenticatedUser, TokenBundle, GoogleIdentity + identity-provider name constants (PASSWORD, GOOGLE, OAUTH_ONLY_SENTINEL_HASH)
├── persistence/                  # SQLite storage layer — async SQLModel over aiosqlite
│   ├── store.py                  #     RuntimeStore facade composing all mixins
│   ├── engine.py                 #     sync engine (bootstrap/migrations) + async engine
│   ├── models.py                 #     SQLModel table classes + helpers (utcnow, new_id)
│   ├── column_types.py           #     TolerantJSONList / ToolInputJSON TypeDecorators
│   ├── schema_version.py         #     in-house PRAGMA user_version migration runner
│   ├── audit_events.py           #     AuditEvent StrEnum (canonical event_type values)
│   ├── errors.py                 #     IdentityConflictError
│   ├── users/                    #     auth-table mixins (users, identities, login failures, email verification, security events)
│   ├── conversations/            #     chat-session mixins (sessions.py, transcripts.py)
│   └── exports/                  #     CSV export library mixin
├── providers/                    # LLM provider adapters
│   ├── base.py                   #     BaseLLMClient ABC
│   ├── types.py                  #     value types (StopReason, Usage, ToolDefinition, ToolUseEvent, ...) + canonical provider names (ANTHROPIC, OPENAI, CODEX)
│   ├── errors.py                 #     LLMError, ContextOverflowError, RetryableError
│   ├── retry.py                  #     header-aware exponential backoff
│   ├── overflow.py               #     context-overflow error classification
│   ├── tool_calls.py             #     tool-call payload parsing helpers
│   └── clients/                  #     concrete clients (anthropic.py, openai.py, codex.py)
└── tools/                        # Tool registry + handlers (used by core/agent)
    ├── registry.py               #     dispatch + execute_tool_structured
    ├── definitions.py            #     tool JSON schemas + TOOLS list
    ├── validation.py             #     JSON Schema input validation + hint injection
    ├── truncate.py               #     result formatters
    ├── guide_registry.py         #     GUIDE_TOPICS enum + GUIDES_DIR
    ├── errors.py                 #     SQLValidationError
    ├── types.py                  #     SQLResult
    ├── handlers/                 #     one file per tool (create_chart, create_csv_export, execute_sql, get_guide, get_schema, player_lookup)
    ├── sandbox/                  #     runner.py (DuckDB execution) + schema_metadata.py
    └── guides/                   #     markdown reference docs the get_guide tool serves

run.py                            # uvicorn entry point → app.main:app
web/                              # Browser UI (index.html + static assets)
tests/                            # pytest test suite
data/                             # Runtime data (runtime.sqlite3 — ignored)
```

## Development

```bash
python3 run.py                    # API server (port 8001)
open web/index.html               # Chat UI
python3 -m pytest tests/          # Run tests

# Build scripts live in the sibling NFLVERSE repo (../NFLVERSE/).
# Run them from that directory — they write to ../NFLVERSE/data/nflverse.duckdb,
# which this app reads via DB_PATH in .env.
```

**Note**: Restart the API server (`python3 run.py`) after changing `core/agent/system_prompt.py` or `core/tools/*` — the running server caches imports.

## Auth & multi-user

Multi-user password auth with open signup. First registrant becomes `role='admin'`; subsequent signups get `role='user'`. Sessions are opaque 32-byte tokens stored in `auth_sessions` (30-day TTL, revocable on logout).

**Session transport**: browser UI uses an `HttpOnly`, `Secure`, `SameSite=Lax` cookie (`session`) plus a JS-readable CSRF cookie (`csrf_token`) echoed in the `X-CSRF-Token` header on mutating requests (double-submit pattern). `Authorization: Bearer …` is still accepted for API clients and the `/docs` tester; Bearer requests skip CSRF (browsers can't auto-attach Authorization cross-origin).

**Password handling**: bcrypt cost 12. Failed logins tracked per-email with progressive delay (0s → 0.25s → 0.5s → 1s → 2s → 4s cap) and hard lockout after `LOGIN_LOCKOUT_MAX_FAILURES` (default 10) attempts for `LOGIN_LOCKOUT_DURATION_SECONDS` (default 900s). Layered on top of the per-IP rate limiter.

**Email verification**: optional (`EMAIL_VERIFICATION_REQUIRED=1`). When on, `/auth/register` returns 202 `{status: "verification_pending"}` instead of a session, a verification link is mailed via Resend, and `/auth/login` rejects unverified accounts until `/auth/verify-email` consumes the token. OAuth-verified identities (future Google login) skip this gate via `_create_user_from_verified_identity(verified=True)`.

**API keys**: Per-user, Fernet-encrypted at rest with the master key in `SETTINGS_ENCRYPTION_KEY`. Plaintext is never returned by any endpoint; ciphertext is decrypted only server-side when invoking the LLM.

**Security headers**: `SecurityHeadersMiddleware` attaches CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy to every response; HSTS added when the request is over HTTPS.

**Audit log**: `security_events` table records login success/failure/locked, logout, password change, account delete, api_key_set/cleared, oauth_linked/unlinked, csrf_rejected. Each event also logged as structured JSON to stderr.

**Log redaction**: `SecretRedactingFilter` masks `sk-ant-*`, `sk-proj-*`, `Bearer *`, and JWT-shaped substrings in every log record so keys pasted into chat messages don't reach stdout or aggregators.

Env vars:
- `SETTINGS_ENCRYPTION_KEY` — required. Generate once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `AUTH_TOKEN_TTL_DAYS` — session lifetime, default 30.
- `ALLOWED_ORIGINS` — CSV of CORS origins. Unset → localhost defaults. Set to prod origin(s) when hosted.
- `ALLOW_NULL_ORIGIN` — `1` to add `null` to the CORS allowlist for `file://` testing. Off by default.
- `REGISTRATION_INVITE_CODE` — optional invite-code gate on `/auth/register`. Rotate by changing the env var and restarting.
- `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS`, `APP_BASE_URL` — required when `EMAIL_VERIFICATION_REQUIRED=1`. `APP_BASE_URL` is used to build the verification link (e.g. `https://nflverse.fly.dev`).
- `EMAIL_VERIFICATION_REQUIRED` — `1` to gate login on verified email. Default `0`.
- `LOGIN_LOCKOUT_MAX_FAILURES`, `LOGIN_LOCKOUT_WINDOW_SECONDS`, `LOGIN_LOCKOUT_DURATION_SECONDS` — per-email lockout tuning, defaults 10/900/900.

### Google OAuth sign-in

Shipped 2026-04-23. Users can sign up / sign in with Google, and existing password users can link their Google account from Settings → Account.

**How it works:**
- `user_identities` table stores one row per linked auth method per user. A user who signs up with a password has a single `('password', email)` row; adding Google adds a second `('google', <sub>)` row. Deleting the user cascades.
- OAuth-only users get `users.password_hash = "!"` — a sentinel that bcrypt treats as malformed, so `verify_password` returns False for any attempt. Avoids a NOT-NULL schema rebuild. Such users have no `password` identity row, which the unlink guard uses to refuse removing their last sign-in method.
- New Google sign-in against an email that already has a password account auto-links (trusts Google's verified email). Same-`sub` sign-ins after that reuse the identity without creating a new row.
- Flow: `/auth/oauth/google/start` generates PKCE + state + nonce, stashes in an in-memory pending-flow registry, 302s to Google. `/auth/oauth/google/callback` verifies state, exchanges the code, verifies the ID token against Google's JWKS (cached 1h), and either issues a session (sign-in flow) or attaches the identity (link flow, started from Settings).
- Both routes are GETs (browser navigation) and CSRF-exempt by the usual safe-method rule — the `state` parameter is the anti-CSRF for the callback. Session cookies from the rest of the app still travel (SameSite=Lax), which is how the callback can tell a link flow (user_id in pending row) from a sign-in flow.
- `security_events` gains `oauth_signin_started`, `oauth_signin_succeeded`, `oauth_signin_failed`, `oauth_link_started`, `oauth_linked`, `oauth_unlinked`, `oauth_link_rejected`.

**Files:** `core/auth/google_oauth.py` (OAuth primitives + ID-token verification), `core/persistence/users/user_identities.py` (mixin), `app/processes/oauth/google/service.py` (flow orchestration), `app/processes/oauth/google/routes.py` (endpoints), `app/processes/settings/routes.py` (link/unlink + list), `core/auth/primitives.py`'s existing `_set_auth_cookies` helper is reused unchanged.

**Env vars:**
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` — set via Google Cloud Console. The "Continue with Google" button and `/auth/oauth/google/*` routes only appear when both are set.
- Redirect URI is derived from `APP_BASE_URL` (`<base>/auth/oauth/google/callback`), so add that URL to the OAuth client's authorized redirects in Google Cloud Console — once for localhost, once for prod.

**Follow-up work:** OAuth-only users can't currently set a password. When we want a "set password" flow (so an OAuth user can turn into a hybrid password+Google user), add `POST /auth/set-password` that requires an authenticated session, rejects if `password_hash != "!"`, writes a real bcrypt hash, and seeds a `password` identity row. Not needed right now since users can also unlink Google (if another identity exists) or keep using OAuth indefinitely.

## Deployment

Hosted (Fly.io) and self-hosted (Caddy + systemd) runbooks live in [docs/deployment.md](docs/deployment.md). That doc is single-origin-TLS, with pre-flight checklists, seed steps for the NFL databases on the Fly volume, and backup/rotation guidance for the encryption key.

## Notes

- 2025 stats available from nflverse native data
- `season_stats.recent_team` is backfilled from `game_stats` (most common team per player-season)
- Kicker stats are in `game_stats`/`season_stats` (fg_made, fg_att, fg_pct, pat_made, etc.)
- `combine` table has no join edges — query separately
- NGS `stat_type`: `passing`/`rushing`/`receiving`; `week=0` = season totals
- PFR `stat_type`: `pass`/`rush`/`rec` (different naming!)
- QBR `game_week` is an INTEGER (1, 2, ...), **no season total rows** — use `AVG(qbr_total)` grouped by player+season. `season_type` is `"Regular"`/`"Postseason"`
