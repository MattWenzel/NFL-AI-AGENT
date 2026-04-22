# nflverse DB

NFL player stats database built from [nflverse](https://github.com/nflverse/nflverse-data) data.

## Quick Reference

| Database | Size | Tables | Rows | Years |
|----------|------|--------|------|-------|
| `nflverse.db` | ~327 MB | 13 | ~2.25M | 1999-2025 |
| `pbp.db` | ~2 GB | 1 | 1.28M | 1999-2025 |

**Full schema**: [NFLVERSE/docs/DATABASE.md](NFLVERSE/docs/DATABASE.md)

## Key Tables

**Core**: `players`, `player_ids`, `games`, `game_stats`, `season_stats`, `draft_picks`, `combine`

**Supplementary**: `snap_counts` (2015+), `ngs_stats` (2016+), `depth_charts` (2001-2024), `depth_charts_2025` (2025, uses `dt` datetime), `pfr_advanced` (2018+), `qbr` (2006-2023)

**Play-by-play**: 1.28M plays in separate `pbp.db` (too large to combine)

## ID System

- **GSIS ID** (`00-0033873`) - Primary key in `players` table
- **player_id** - Used by `game_stats`, `season_stats` (same format as gsis_id but different column name — join: `players.gsis_id = game_stats.player_id`)
- **PFR ID** (`MahoPa00`) - Used by `snap_counts`, `pfr_advanced`
- **ESPN ID** (`3139477`) - Used by `qbr`
- Join via `player_ids` table for cross-reference

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

Organized by subsystem, not by layer. Top-level folders each own a concern:

```
agent/                      # LLM conversation domain
├── runtime.py              #   ChatRuntime: prepare_session, run_session
├── runtime_policy.py       #   RuntimeLoopState + raise_if_doom_loop
├── turn_manager.py         #   AssistantTurnManager (assistant turn lifecycle)
├── tool_execution.py       #   ToolExecutionService (tool dispatch + persistence)
├── persistence.py          #   RuntimePersistence (write-side store boundary)
├── events.py               #   RuntimeEvent dataclass + RuntimeLoopError
├── compaction.py           #   estimate_active_tokens, compact_if_needed
├── summarizer.py           #   LLM-backed summarization for compaction
├── token_counting.py       #   cl100k token estimator for compaction decisions
├── message_builder.py      #   transcript → wire-format message list
├── system_prompt.py        #   slim base prompt (~2.3K tokens: rules + guide index)
└── __init__.py             #   package marker

tools/                      # Tool registry + implementations (flat)
├── __init__.py             #   re-exports TOOLS, TOOL_DEFINITIONS, execute_tool*
├── definitions.py          #   tool schemas + TOOLS typed list
├── registry.py             #   dispatch table + registry drift guard
├── validation.py           #   JSON Schema input validation + error hint injection
├── sandbox.py              #   read-only SQL with timeout, row limit, PBP auto-attach
├── truncate.py             #   truncate_text, truncate_rows (shared result formatters)
├── schema_metadata.py      #   TABLE_ALIASES, TABLE_DATABASE, JOIN_EDGES
├── execute_sql.py          #   handlers — flat, one file per tool
├── player_lookup.py
├── get_schema.py
├── get_guide.py
├── create_chart.py
└── create_csv_export.py

auth/                       # Authentication subsystem (primitives + OAuth + encryption)
├── primitives.py           #   password hashing, bearer tokens, get_current_user dep
├── encryption.py           #   Fernet wrapper for encrypted API keys / OAuth bundles
└── codex_oauth.py          #   ChatGPT device-code OAuth protocol

provider/                   # LLM provider adapters
├── __init__.py             #   registry + factory (create_client, list_providers, ...)
├── base.py                 #   BaseLLMClient ABC + canonical types
├── anthropic.py            #   Anthropic Claude
├── openai.py               #   OpenAI GPT (optional SDK)
├── codex.py                #   OpenAI Codex (ChatGPT OAuth) via internal Responses API
├── retry.py                #   shared header-aware exponential backoff
└── overflow.py             #   context-overflow error detection

storage/                    # Runtime persistence — async SQLModel over aiosqlite
├── __init__.py             #   re-exports RuntimeStore + SQLModel record classes
├── store.py                #   RuntimeStore(UsersMixin, SessionStoreMixin, TranscriptStoreMixin, ExportsMixin); owns engines, per-session lock, startup reconcile
├── models.py               #   SQLModel table classes (SessionRecord, TurnRecord, ToolRunRecord, etc.) + helpers (utcnow, new_id, wrap_summaries_for_prompt)
├── engine.py               #   sync engine (bootstrap/migrations) + async engine (aiosqlite) + async_sessionmaker
├── types.py                #   TolerantJSONList / ToolInputJSON TypeDecorators (log-and-recover on malformed rows)
├── users.py                #   UsersMixin (users, auth_sessions, user_api_keys)
├── session_store.py        #   SessionStoreMixin (session lifecycle + list-row projection)
├── transcript_store.py     #   TranscriptStoreMixin (turns, parts, tool runs, compaction, transcript assembly)
├── exports.py              #   ExportsMixin (CSV export registry CRUD)
└── migrations/             #   Alembic scaffolding (env.py + versions/); schema evolves via revisions

server/                     # HTTP transport (FastAPI)
├── app.py                  #   app factory + lifespan (validates DBs, wires store+runtime)
├── dependencies.py         #   get_store, get_runtime, create_client_for_request
├── sse.py                  #   RuntimeEvent → SSE dict serialization
├── rate_limit.py           #   in-memory sliding-window limiter for auth endpoints
├── logging.py              #   setup_logging()
├── schemas/                #   Pydantic wire-format models, split per domain
│   ├── chat.py             #     /chat/message + /chat/stream payloads
│   ├── conversations.py    #     conversation list / transcript / update
│   ├── exports.py          #     CSV library list / detail / rename
│   ├── auth.py             #     register / login / status / password / delete
│   ├── settings.py         #     per-user API key CRUD
│   ├── providers.py        #     provider listing
│   └── codex_oauth.py      #     device-code start / status responses
└── routes/
    ├── chat.py             #   POST /chat/message, POST /chat/stream
    ├── conversations.py    #   list / transcript / delete
    ├── providers.py        #   GET /chat/providers
    ├── auth.py             #   register, login, logout, password change, delete account
    ├── settings.py         #   per-user API key CRUD
    ├── codex_oauth.py      #   ChatGPT OAuth device-code start/status/cancel
    ├── csv_library.py      #   CSV export library CRUD (/chat/exports)
    └── csv_downloads.py    #   GET /exports/{filename} download endpoint

run.py                      # HTTP server entry point (python3 run.py)
config.py                   # DB paths, runtime db path, load_dotenv()
web/                        # Browser UI (index.html + static assets)
tests/                      # pytest test suite
data/                       # Runtime data (runtime.sqlite3 — ignored)
```

## Development

```bash
python3 run.py                    # API server (port 8001)
open web/index.html               # Chat UI
python3 -m pytest tests/          # Run tests

# Build scripts (in NFLVERSE/)
python3 NFLVERSE/scripts/download.py                      # Fetch raw parquet into data/raw/
python3 NFLVERSE/scripts/build_db.py --all                # Core DB (from local parquet)
python3 NFLVERSE/scripts/build_db.py --pbp --all          # Play-by-play (from local parquet)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --all      # Fallback: core DB via nflreadpy (network)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --pbp --all # Fallback: PBP via nflreadpy (network)
python3 NFLVERSE/scripts/check_updates.py                 # Check which tables/years are stale
```

**Note**: Restart the API server (`python3 run.py`) after changing `agent/system_prompt.py` or `tools/*` — the running server caches imports.

## Auth & multi-user

Multi-user password auth with open signup. First registrant becomes `role='admin'`; subsequent signups get `role='user'`. Sessions are opaque 32-byte bearer tokens stored in `auth_sessions` (30-day TTL, revocable on logout). Per-user API keys are Fernet-encrypted at rest with the master key in `SETTINGS_ENCRYPTION_KEY`. Registration/login are rate-limited per IP (5/15min and 10/15min).

Env vars:
- `SETTINGS_ENCRYPTION_KEY` — required. Generate once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `AUTH_TOKEN_TTL_DAYS` — session lifetime, default 30.
- `ALLOWED_ORIGINS` — CSV of CORS origins. Unset → localhost defaults only. Set to your prod origin(s) when hosted.
- `REGISTRATION_INVITE_CODE` — optional invite-code gate. When set, `/auth/register` rejects signups without a matching code (403). When unset, registration is open. Share the code out-of-band with anyone you want to let in; rotate by changing the env var and restarting.

### OAuth migration path (deferred)

When Google OAuth ships, the following six-step plan picks up from the current state. Don't half-land any of it — when it's time, do all six in one branch:

1. `pip install authlib` (or `google-auth` + `google-auth-oauthlib`); add to `requirements.txt`.
2. New table `user_identities(id, user_id, provider, provider_subject, created_at, UNIQUE(provider, provider_subject))`. On migration, seed one `('password', user.email)` row per existing user for consistency. Also rebuild `users` to drop NOT NULL on `password_hash` (SQLite requires a table rebuild — do it in a separate commit with a pre-flight backup).
3. New endpoints in `server/routes/auth.py`:
   - `GET /auth/oauth/google/start` — PKCE + state, 302 to Google.
   - `GET /auth/oauth/google/callback` — exchange code, verify `id_token`, look up by `(provider='google', provider_subject=sub)`. If not found, look up by email: link if an existing password user matches, else call `_create_user_from_verified_identity(email=…, password_hash=None, verified=True)`. Issue session via `_issue_session`.
4. Frontend: render a "Continue with Google" button in the reserved `.auth-alt` slot (`web/static/js/auth.js`); point it at `/auth/oauth/google/start`.
5. Settings modal: add an "Account" section listing linked identities, with unlink buttons. Guard: don't let a user unlink their last identity if they have no password.
6. Google Cloud Console: create OAuth client, set authorized redirect URI to `<prod-url>/auth/oauth/google/callback` (and `http://localhost:8001/auth/oauth/google/callback` for dev).

The tail `_create_user_from_verified_identity` → `_issue_session` path in `server/routes/auth.py` is already shaped so the OAuth callback reuses it unchanged — the password and OAuth flows differ only in how they produce a verified email.

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
