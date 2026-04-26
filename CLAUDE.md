# nflverse DB

NFL player stats database built from [nflverse](https://github.com/nflverse/nflverse-data) data.

## Quick Reference

| Database | Size | Tables | Rows | Years |
|----------|------|--------|------|-------|
| `nflverse.duckdb` | ~1.2 GB | 25 + 1 view | ~5M | 1999-2025 |

Single DuckDB file with **78 FK constraints** enforced at build time. Accessed read-only by the chat agent via `backend/lib/tools/sandbox/runner.py` (raw `duckdb.connect(..., read_only=True)`); no ORM involvement.

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

Top-level split: `backend/` holds the Python server, `frontend/` holds the browser UI. Inside `backend/`, the layering is: `server/` is the FastAPI HTTP boundary, `services/` holds application services (one per HTTP feature — each subfolder exports a `FooService` class), and `lib/` holds the framework-free libraries those services consume (`agent/`, `providers/`, `tools/`, `db/`, `auth/`). Inside `lib/db/` the SQL boundary is explicit: `lib/db/sql/` contains every file that imports SQLAlchemy/SQLModel (tables, RuntimeStore, per-domain query mixins, engine, migrations); `lib/db/types/` contains plain-Python value types (enums, exceptions) with zero SQL imports. `services/` itself is FastAPI-free — verifiable: `grep -rE "fastapi|starlette" backend/services/` returns empty. The frontend mirrors the feature-slice convention for its JS modules.

```
backend/
├── server/                       # FastAPI HTTP boundary
│   ├── app.py                    #   app factory + lifespan; uvicorn entry (backend.server.app:app)
│   ├── routes/                   #   HTTP handlers by app process
│   ├── dependencies.py           #   FastAPI Depends factories (get_store, get_runtime, ...)
│   ├── csrf.py                   #   double-submit CSRF dependency
│   ├── session.py                #   session token parsing + browser auth cookie behavior
│   ├── request_context.py        #   request → audit/client context helpers
│   ├── middleware.py             #   SecurityHeadersMiddleware (CSP / HSTS / etc.)
│   ├── sse.py                    #   RuntimeEvent → SSE dict serialization
│   ├── startup.py                #   DB validation, runtime wiring, housekeeping
│   ├── logging.py                #   setup_logging + secret-redacting filter
│   ├── process_state.py          #   AppProcessState + API-facing limiters
│   └── rate_limit.py             #   per-IP RateLimiter + concurrency limiter
├── services/                     # Application services (one per HTTP feature) — services, DTOs, schemas, errors
│   ├── auth/                     #   register / login / logout / password / delete / verify / resend
│   ├── chat/                     #   chat orchestration and response aggregation
│   ├── conversations/            #   list / transcript / patch / delete orchestration
│   ├── exports/                  #   CSV library CRUD
│   ├── oauth/                    #   provider_credentials.py plus Codex/Google flow packages
│   ├── providers.py              #   provider response models
│   └── settings.py               #   per-user API key CRUD + linked identity services
├── lib/                          # Framework-free libraries consumed by server/ + services/
│   ├── agent/                    #   Chat runtime loop, events, prompt, compaction
│   ├── auth/                     #   Auth primitives, encryption, OAuth protocol helpers, audit log, lifecycle
│   ├── db/                       #   SQLite persistence — the SQL boundary
│   │   ├── sql/                  #     SQLAlchemy/SQLModel imports live here (tables, store, mixins, migrations)
│   │   └── types/                #     Plain-Python value types — no SQL imports (audit_events, errors)
│   ├── providers/                #   LLM provider registry, types, errors, clients
│   └── tools/                    #   Tool definitions, registry, handlers, SQL sandbox, guides
├── runtime_state.py              # Framework-free lock registries + pending OAuth flows
└── config.py                     # DB paths, env loading, runtime settings

frontend/                         # Browser UI (served at / by FastAPI; assets under /static)
├── index.html                    #   shell + ES-module entrypoint
└── static/
    ├── css/
    │   ├── base/                 #     theme, layout
    │   ├── components/           #     sidebar, composer
    │   └── features/             #     chat, exports, inspector, settings
    └── js/
        ├── app/                  #     main.js (boot + event wiring), render.js (top-level render orchestrator)
        ├── core/                 #     api.js, state.js, utils.js, charts.js
        ├── components/           #     small reusable widgets (e.g. confirm dialog)
        └── features/             #     mirrors backend services; UI-only siblings (inspector, navigation) live here too
            ├── auth/             #       sign-in / sign-up flow
            ├── chat/             #       streaming + thread rendering
            ├── conversations/    #       sidebar list + open
            ├── exports/          #       CSV library + viewer
            ├── inspector/        #       right-pane tool-call details
            ├── navigation/       #       sidebar view switcher
            └── settings/         #       account + provider keys + identities

run.py                            # uvicorn entry point → backend.server.app:app
tests/                            # pytest test suite
data/                             # Runtime data (runtime.sqlite3 — ignored)
```

## Development

```bash
python3 run.py                    # API server (port 8001) — UI served at /
python3 -m pytest tests/          # Run tests

# Build scripts live in the sibling NFLVERSE repo (../NFLVERSE/).
# Run them from that directory — they write to ../NFLVERSE/data/nflverse.duckdb,
# which this app reads via DB_PATH in .env.
```

**Note**: Restart the API server (`python3 run.py`) after changing `backend/lib/agent/system_prompt.py` or `backend/lib/tools/*` — the running server caches imports.

## Auth & multi-user

Multi-user password auth with open signup. First registrant becomes `role='admin'`; subsequent signups get `role='user'`. Sessions are opaque 32-byte tokens stored in `auth_sessions` (30-day TTL, revocable on logout).

**Session transport**: browser UI uses an `HttpOnly`, `Secure`, `SameSite=Lax` cookie (`session`) plus a JS-readable CSRF cookie (`csrf_token`) echoed in the `X-CSRF-Token` header on mutating requests (double-submit pattern). `Authorization: Bearer …` is still accepted for API clients and the `/docs` tester; Bearer requests skip CSRF (browsers can't auto-attach Authorization cross-origin).

**Password handling**: bcrypt cost 12. Failed logins tracked per-email with progressive delay (0s → 0.25s → 0.5s → 1s → 2s → 4s cap) and hard lockout after `LOGIN_LOCKOUT_MAX_FAILURES` (default 10) attempts for `LOGIN_LOCKOUT_DURATION_SECONDS` (default 900s). Layered on top of the per-IP rate limiter.

**Email verification**: optional (`EMAIL_VERIFICATION_REQUIRED=1`). When on, `/auth/register` returns 202 `{status: "verification_pending"}` instead of a session, a verification link is mailed via Resend, and `/auth/login` rejects unverified accounts until `/auth/verify-email` consumes the token. OAuth-verified identities skip this gate via `create_user_account(..., verified=True)` in `backend/lib/auth/lifecycle.py`.

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

**Files:** `backend/lib/auth/google_oauth.py` (OAuth primitives + ID-token verification), `backend/lib/db/sql/users/identities.py::UserIdentitiesMixin` (storage), `backend/services/oauth/google/service.py` (flow orchestration), `backend/server/routes/oauth_google.py` (endpoints), `backend/server/routes/settings.py` (link/unlink + list), and `backend/server/session.py` for shared session cookie behavior.

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
