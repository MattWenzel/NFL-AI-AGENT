# nflverse DB

NFL player stats database built from [nflverse](https://github.com/nflverse/nflverse-data) data.

## Quick Reference

| Database | Size | Tables | Rows | Years |
|----------|------|--------|------|-------|
| `nflverse.duckdb` | ~1.2 GB | 25 + 1 view | ~5M | 1999-2025 |

Single DuckDB file with **78 FK constraints** enforced at build time. Accessed read-only by the chat agent via `backend/domain/tools/sandbox/runner.py` (raw `duckdb.connect(..., read_only=True)`); no ORM involvement.

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

The env-var keys are a fallback for users who haven't stored their own key in Settings — gated by `SHARED_PROVIDER_KEYS` (`admin` default: only `role='admin'` users spend on the server's keys; `all` / `none` to widen or close). Everyone else gets "No API key — add one in Settings."

The chat runtime is transcript-backed: sessions, turns, assistant parts, tool runs, and compaction summaries are persisted in `data/runtime.sqlite3`. Long conversations are compacted by summarizing older turns and excluding older raw tool output from active prompt context while keeping the full transcript in storage.

### Architecture

Top-level split: `backend/` holds the Python server, `frontend/` holds the browser UI (React + Vite + Tailwind + shadcn/ui). Inside `backend/`, the layering is onion-style: `api/` is the FastAPI HTTP boundary (routes + Pydantic wire DTOs + `Depends` factories), `application/` holds the use-case services (one module per HTTP feature, each exporting a `FooService` class), `domain/` holds the framework-free libraries those services consume (`agent/`, `providers/`, `tools/`, `auth/`), and `data/` holds persistence — the SQL boundary. `data/` itself imports SQLAlchemy/SQLModel; `data/types/` contains plain-Python value types (enums, exceptions) with zero SQL imports. `server/` is pure HTTP infrastructure (middleware, logging, csrf, session, sse, startup) — no routes, those moved to `api/`. `application/` itself is FastAPI-free — verifiable: `grep -rE "fastapi|starlette" backend/application/` returns empty. The frontend mirrors the feature-slice convention for its component / lib modules.

The chat UI is one of three top-level surfaces in the React app: **Chat** (regular agent conversations, persistent), **Reports** (a Report is a session of `kind="table_chat"` — a pinned query result that the user can iterate on with the agent rewriting the SQL via `set_table`; lock-toggle on the toolbar freezes the SQL), and **Database** (read-only schema browser + SQL editor with a small ephemeral helper chat in the right pane, backed by a separate stateless agent loop in `backend/domain/agent/stateless.py`).

```
backend/
├── api/                          # HTTP layer — FastAPI routes + wire DTOs
│   ├── routes/                   #   HTTP handlers per feature
│   ├── schemas/                  #   Pydantic request/response models
│   └── dependencies.py           #   FastAPI Depends factories (get_store, get_runtime, ...)
├── application/                  # Use-case services (one module per HTTP feature)
│   ├── auth.py                   #   register / login / logout / password / delete / verify / resend
│   ├── chat.py                   #   chat orchestration and response aggregation
│   ├── conversations.py          #   list / transcript / patch / delete orchestration
│   ├── tables.py                 #   Reports (table_chat) CRUD + live-table state
│   ├── database.py               #   read-only DuckDB schema/preview browser
│   ├── db_helper_chat.py         #   ephemeral SQL helper chat (no persistence)
│   ├── exports.py                #   CSV library CRUD
│   ├── oauth/
│   │   ├── provider_credentials.py  # ProviderCredentialService (api_key vs codex_oauth dispatch)
│   │   ├── codex.py                 # Codex device flow + access-token resolution
│   │   └── google.py                # Google sign-in / sign-up / link / unlink
│   └── settings.py               #   per-user API key CRUD + linked identity services
├── domain/                       # Framework-free libraries consumed by application/
│   ├── agent/                    #   chat runtime loop, events, prompt, compaction;
│   │                             #   plus stateless.py — the helper-chat agent loop (no persistence)
│   ├── auth/                     #   auth primitives, encryption, OAuth protocol helpers, audit log, lifecycle
│   ├── providers/                #   LLM provider registry, types, errors, clients
│   └── tools/                    #   tool definitions, registry, handlers, DuckDB SQL sandbox, guides
├── data/                         # Persistence layer — the SQL boundary
│   ├── models.py                 #   SQLModel table classes
│   ├── projections.py            #   composite read shapes (SessionListEntry, SessionTranscript)
│   ├── store.py                  #   RuntimeStore facade composing per-domain mixins
│   ├── migrations.py             #   schema migrations
│   ├── database.py               #   engine + sessionmaker construction
│   ├── column_types.py           #   custom SQLAlchemy types
│   ├── repositories/             #   per-domain query mixins
│   │   ├── conversations/        #     sessions + transcripts mixins
│   │   ├── exports.py            #     exports CRUD mixin
│   │   └── users/                #     users + identities + login_failures + email_verification + security_events
│   └── types/                    #   plain-Python value types — no SQL imports (audit_events, errors)
├── server/                       # HTTP infrastructure (no routes — those live in api/)
│   ├── app.py                    #   app factory + lifespan; uvicorn entry (backend.server.app:app)
│   ├── csrf.py                   #   double-submit CSRF dependency
│   ├── session.py                #   session token parsing + browser auth cookie behavior
│   ├── request_context.py        #   request → audit/client context helpers + request_id contextvar
│   ├── middleware.py             #   RequestIDMiddleware + SecurityHeadersMiddleware (CSP / HSTS / etc.)
│   ├── sse.py                    #   RuntimeEvent → SSE dict serialization
│   ├── startup.py                #   DB validation, runtime wiring, housekeeping
│   ├── logging.py                #   setup_logging + request-id filter + secret-redacting filter
│   ├── process_state.py          #   AppProcessState + API-facing limiters
│   └── rate_limit.py             #   per-IP RateLimiter + concurrency limiter
├── runtime_state.py              # Framework-free lock registries + pending OAuth flows
└── config.py                     # DB paths, env loading, runtime settings

frontend/                         # Browser UI — React + Vite + Tailwind + shadcn/ui
├── index.html                    #   Vite entry
├── vite.config.ts                #   build config — output goes to frontend/dist/
└── src/
    ├── App.tsx                   #   top-level shell: chat / reports / database routing + state
    ├── main.tsx                  #   React entry
    ├── components/
    │   ├── auth/                 #     sign-in / sign-up
    │   ├── chat/                 #     transcript renderer, streaming, exchange grouping
    │   ├── command/              #     command palette
    │   ├── composer/             #     message composer + provider/model/tool-choice dropdowns
    │   ├── database/             #     Database tab — schema browser, SQL editor, helper chat
    │   ├── inspector/            #     right-pane tool-call details
    │   ├── layout/               #     AppShell (sidebar + main + inspector / alternateInspector)
    │   ├── settings/             #     account + provider keys + linked identities
    │   ├── sidebar/              #     conversation/report list with column-search
    │   ├── tables/               #     Reports view — TableChatView, save flow
    │   ├── theme/                #     theme tokens + provider
    │   ├── thread/               #     transcript renderer + UserTurn / AgentResponse / ToolPayload (shared by Chat and Reports)
    │   └── ui/                   #     shadcn primitives
    └── lib/
        ├── api/                  #   HTTP clients — every fetch goes through here
        │   ├── index.ts          #     apiFetch / apiGet / apiPost / apiPatch / apiDelete + ApiError + CSRF
        │   ├── sse.ts            #     openSseStream() — POST + EventSource reader
        │   ├── database.ts       #     /database/* API client
        │   ├── tables.ts         #     /reports + /tables API client
        │   └── settings.ts       #     /settings/* API client
        ├── state/                #   stores, contexts, and stateful hooks
        │   ├── chatStore.ts      #     central chat state (sessions, exchanges, streaming)
        │   ├── chatContext.tsx   #     React context for the chat store
        │   ├── tablesStore.ts    #     Reports/table_chat state
        │   ├── tablesContext.tsx #     context for the Reports view
        │   ├── activeTable.ts    #     current Report selection
        │   ├── dbHelperChat.ts   #     useDbHelperChat() — stateless helper hook
        │   ├── auth.ts           #     useAuth() + /auth/* calls
        │   └── providers.ts      #     useProviders() — LLM provider registry mirror
        ├── transcript.ts         #   pure transcript helpers (sliceForExchange, hasSqlPayload, ...)
        ├── normalizedMessage.ts  #   common message shape + adapters from each store
        ├── useScrollToBottom.ts  #   smooth-on-send / instant-on-switch scroll hook
        ├── csv.ts                #   CSV download helpers
        ├── theme.ts              #   theme storage
        ├── datetime.ts           #   formatting helpers
        ├── utils.ts              #   cn() — tailwind-merge + clsx
        └── types.ts              #   shared TypeScript types (mirrors backend wire shapes)

run.py                            # uvicorn entry point → backend.server.app:app
tests/                            # pytest test suite
data/                             # Runtime data (runtime.sqlite3 — ignored)
```

### Reports + Database surfaces

`TableChatService` has a wider surface than `ConversationService` because a Report carries extra state on top of a normal session: the live `TableStateRecord` (columns + rows + last SQL) and its `locked` flag. So beyond the standard list/get/patch/delete, it owns `create_table_chat`, `set_table_locked`, `run_and_persist_sql` (user-edited SQL from the Reports view's editable panel — counterpart to the agent's `set_table` tool), and `save_to_reports` (snapshot the live table to a CSV in the exports library). Streaming still goes through `/chat/stream` — the request body shape is identical.

`DbHelperChatService` is separate from `ChatService` because the helper chat is **stateless by design** — refresh wipes it, no `sessions` row, no transcript persistence. It mirrors the `prepare(...)` / `stream_events(...)` shape of `ChatService` so the route layout (concurrency-slot acquire → prepare → producer/consumer SSE loop with heartbeat → release in `finally`) is parallel; the shared `chat_stream_limiter` covers both surfaces so a single user can't exceed the LLM stream cap by mixing them. Provider/credential resolution is shared via `ProviderCredentialService.resolve_provider_client` — both services delegate the default-fallback → registry lookup → key fetch → `create_client` dance there.

`DatabaseService` (the schema browser + ad-hoc SELECT runner) is intentionally a thin composition over the existing sandbox and `TableChatService`: `save_query_as_report` calls `TableChatService.create_table_chat`, then seeds the live table state directly. It never hand-rolls session creation.

## Development

```bash
python3 run.py                    # API server (port 8001) — serves frontend/dist if built;
                                  #   `/` 404s until you've run `npm run build`
python3 -m pytest tests/          # Run backend tests

# Frontend (React + Vite). Two modes:
cd frontend && npm install && npm run dev    # Vite dev server with HMR (separate port)
cd frontend && npm run build                 # Production build → frontend/dist/
                                             #   (then `python3 run.py` serves it at /)

# Build scripts for the NFL data live in the sibling NFLVERSE repo (../NFLVERSE/).
# Run them from that directory — they write to ../NFLVERSE/data/nflverse.duckdb,
# which this app reads via DB_PATH in .env.
```

**Note**: Restart the API server (`python3 run.py`) after changing `backend/domain/agent/system_prompt.py` or `backend/domain/tools/*` — the running server caches imports.

## Auth & multi-user

Multi-user password auth with open signup. First registrant becomes `role='admin'`; subsequent signups get `role='user'`. Sessions are opaque 32-byte tokens stored in `auth_sessions` (30-day TTL, revocable on logout).

**Session transport**: browser UI uses an `HttpOnly`, `Secure`, `SameSite=Lax` cookie (`session`) plus a JS-readable CSRF cookie (`csrf_token`) echoed in the `X-CSRF-Token` header on mutating requests (double-submit pattern). `Authorization: Bearer …` is still accepted for API clients and the `/docs` tester; Bearer requests skip CSRF (browsers can't auto-attach Authorization cross-origin).

**Password handling**: bcrypt cost 12. Failed logins tracked per-email with progressive delay (0s → 0.25s → 0.5s → 1s → 2s → 4s cap) and hard lockout after `LOGIN_LOCKOUT_MAX_FAILURES` (default 10) attempts for `LOGIN_LOCKOUT_DURATION_SECONDS` (default 900s). Layered on top of the per-IP rate limiter.

**Email verification**: optional (`EMAIL_VERIFICATION_REQUIRED=1`). When on, `/auth/register` returns 202 `{status: "verification_pending"}` instead of a session, a verification link is mailed via Resend, and `/auth/login` rejects unverified accounts until `/auth/verify-email` consumes the token. OAuth-verified identities skip this gate via `create_user_account(..., verified=True)` in `backend/domain/auth/lifecycle.py`.

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
- `SHARED_PROVIDER_KEYS` — who may spend on the server's env-var LLM keys: `admin` (default), `all`, or `none`.
- `CHAT_REQUESTS_PER_QUARTER_HOUR` (default 150), `SQL_REQUESTS_PER_QUARTER_HOUR` (default 300) — per-user volume caps (sliding 15-min window), layered under `CHAT_STREAM_MAX_PER_USER`. Users bring their own LLM keys, so these protect server CPU/bandwidth from scripted clients, not token spend — keep them generous.
- `MAX_EXPORTS_PER_USER` — CSV library size cap per user, default 200.

### Google OAuth sign-in

Shipped 2026-04-23. Users can sign up / sign in with Google, and existing password users can link their Google account from Settings → Account.

**How it works:**
- `user_identities` table stores one row per linked auth method per user. A user who signs up with a password has a single `('password', email)` row; adding Google adds a second `('google', <sub>)` row. Deleting the user cascades.
- OAuth-only users get `users.password_hash = "!"` — a sentinel that bcrypt treats as malformed, so `verify_password` returns False for any attempt. Avoids a NOT-NULL schema rebuild. Such users have no `password` identity row, which the unlink guard uses to refuse removing their last sign-in method.
- New Google sign-in against an email that already has a password account auto-links **only if that account's email is verified** (`email_verified_at` set). Unverified accounts are refused (`UnverifiedAccountAutoLinkError` → `/?oauth_error=account_unverified`) — otherwise whoever pre-registered the email could capture the OAuth user's session (pre-hijack). The account owner can still sign in with their password and link from Settings. Same-`sub` sign-ins after linking reuse the identity without creating a new row.
- Flow: `/auth/oauth/google/start` generates PKCE + state + nonce, stashes in an in-memory pending-flow registry, 302s to Google. `/auth/oauth/google/callback` verifies state, exchanges the code, verifies the ID token against Google's JWKS (cached 1h), and either issues a session (sign-in flow) or attaches the identity (link flow, started from Settings).
- Both routes are GETs (browser navigation) and CSRF-exempt by the usual safe-method rule — the `state` parameter is the anti-CSRF for the callback. Session cookies from the rest of the app still travel (SameSite=Lax), which is how the callback can tell a link flow (user_id in pending row) from a sign-in flow.
- `security_events` gains `oauth_signin_started`, `oauth_signin_succeeded`, `oauth_signin_failed`, `oauth_link_started`, `oauth_linked`, `oauth_unlinked`, `oauth_link_rejected`.

**Files:** `backend/domain/auth/google_oauth.py` (OAuth primitives + ID-token verification), `backend/data/repositories/users/identities.py::UserIdentitiesMixin` (storage), `backend/application/oauth/google.py` (flow orchestration), `backend/api/routes/oauth_google.py` (endpoints), `backend/api/routes/settings.py` (link/unlink + list), and `backend/server/session.py` for shared session cookie behavior.

**Env vars:**
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` — set via Google Cloud Console. The "Continue with Google" button and `/auth/oauth/google/*` routes only appear when both are set.
- Redirect URI is derived from `APP_BASE_URL` (`<base>/auth/oauth/google/callback`), so add that URL to the OAuth client's authorized redirects in Google Cloud Console — once for localhost, once for prod.

**Follow-up work:** OAuth-only users can't currently set a password. When we want a "set password" flow (so an OAuth user can turn into a hybrid password+Google user), add `POST /auth/set-password` that requires an authenticated session, rejects if `password_hash != "!"`, writes a real bcrypt hash, and seeds a `password` identity row. Not needed right now since users can also unlink Google (if another identity exists) or keep using OAuth indefinitely.

## Notes

- 2025 stats available from nflverse native data
- `season_stats.recent_team` is backfilled from `game_stats` (most common team per player-season)
- Kicker stats are in `game_stats`/`season_stats` (fg_made, fg_att, fg_pct, pat_made, etc.)
- `combine` table has no join edges — query separately
- NGS `stat_type`: `passing`/`rushing`/`receiving`; `week=0` = season totals
- PFR `stat_type`: `pass`/`rush`/`rec` (different naming!)
- QBR `game_week` is an INTEGER (1, 2, ...), **no season total rows** — use `AVG(qbr_total)` grouped by player+season. `season_type` is `"Regular"`/`"Postseason"`
