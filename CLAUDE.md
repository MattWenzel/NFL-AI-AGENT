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
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | 200K |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` | 128K |

Select via `CHAT_PROVIDER` env var (default: `anthropic`), CLI `--provider` flag, or UI dropdown.

The chat runtime is transcript-backed: sessions, turns, assistant parts, tool runs, and compaction summaries are persisted in `data/runtime.sqlite3`. Long conversations are compacted by summarizing older turns and excluding older raw tool output from active prompt context while keeping the full transcript in storage.

### Architecture

Layered by flow-of-data: `api/` (HTTP transport) → `agent/` (domain: runtime + tools + prompts) → `infra/` (adapters to LLM SDKs + SQLite). `cli/` is an alternate entry point that drives the same `agent/` runtime.

```
api/                        # HTTP transport — thin routers
├── main.py                 #   app factory + lifespan (validates DBs, wires store+runtime)
├── dependencies.py         #   get_store, get_runtime, create_client_for_request, close_client
├── schemas.py              #   all request/response Pydantic models
├── sse.py                  #   RuntimeEvent → SSE dict serialization
└── routers/
    ├── chat.py             #   POST /chat/message, POST /chat/stream
    ├── conversations.py    #   list / transcript / delete
    ├── providers.py        #   GET /chat/providers
    └── exports.py          #   GET /exports/{filename}

agent/                      # Domain — runtime + tools + prompts
├── runtime/
│   ├── __init__.py         #   re-exports ChatRuntime, RuntimeEvent, RuntimeLoopError, TOOLS
│   ├── loop.py             #   ChatRuntime: run_session, prepare_session, _execute_tool
│   ├── events.py           #   RuntimeEvent dataclass + RuntimeLoopError
│   ├── compaction.py       #   estimate_active_tokens, compact_if_needed
│   └── loop_detector.py    #   raise_if_doom_loop
├── prompts/
│   ├── system.py           #   base prompt (~5K tokens of DB knowledge)
│   └── hints.py            #   per-provider supplemental hints + get_system_prompt
└── tools/
    ├── __init__.py         #   re-exports TOOL_DEFINITIONS, TOOLS, execute_tool*
    ├── definitions.py      #   tool schemas + TOOLS typed list
    ├── dispatch.py         #   dispatch table + registry drift guard
    ├── validation.py       #   input validation + error hint injection
    ├── sql_sandbox.py      #   read-only SQL with timeout, row limit, PBP auto-attach
    └── handlers/
        ├── execute_sql.py
        ├── player_lookup.py
        ├── get_schema.py
        └── create_csv_export.py

infra/                      # External adapters
├── providers/
│   ├── __init__.py         #   registry, factory (create_client), ProviderInfo
│   ├── base.py             #   BaseLLMClient ABC + canonical types
│   ├── anthropic.py        #   Anthropic Claude
│   └── openai.py           #   OpenAI GPT (optional SDK)
├── persistence/
│   └── runtime_store.py    #   SQLite transcript store (sessions, turns, parts, tool runs)
└── logger.py               #   setup_logging()

cli/
└── chat_cli.py             #   python3 -m cli.chat_cli (drives ChatRuntime directly)

config.py                   # DB paths, runtime db path, load_dotenv()
run.py                      # Server entry point
chat.html                   # Browser UI (SSE streaming, provider selection)
tests/                      # pytest test suite
data/                       # Runtime data (runtime.sqlite3 — ignored)
```

## Development

```bash
python3 run.py                    # API server (port 8001)
python3 -m cli.chat_cli           # AI chat agent (default: Anthropic)
python3 -m cli.chat_cli -p openai # Use OpenAI
open chat.html                    # Chat UI
python3 -m pytest tests/          # Run tests

# Build scripts (in NFLVERSE/)
python3 NFLVERSE/scripts/download.py                      # Fetch raw parquet into data/raw/
python3 NFLVERSE/scripts/build_db.py --all                # Core DB (from local parquet)
python3 NFLVERSE/scripts/build_db.py --pbp --all          # Play-by-play (from local parquet)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --all      # Fallback: core DB via nflreadpy (network)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --pbp --all # Fallback: PBP via nflreadpy (network)
python3 NFLVERSE/scripts/check_updates.py                 # Check which tables/years are stale
```

**Note**: Restart the API server (`python3 run.py`) after changing `agent/prompts/system.py` or `agent/tools/*` — the running server caches imports.

## Auth & multi-user

Multi-user password auth with open signup. First registrant becomes `role='admin'`; subsequent signups get `role='user'`. Sessions are opaque 32-byte bearer tokens stored in `auth_sessions` (30-day TTL, revocable on logout). Per-user API keys are Fernet-encrypted at rest with the master key in `SETTINGS_ENCRYPTION_KEY`. Registration/login are rate-limited per IP (5/15min and 10/15min).

Env vars:
- `SETTINGS_ENCRYPTION_KEY` — required. Generate once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- `AUTH_TOKEN_TTL_DAYS` — session lifetime, default 30.
- `ALLOWED_ORIGINS` — CSV of CORS origins. Unset → localhost defaults only. Set to your prod origin(s) when hosted.

### OAuth migration path (deferred)

When Google OAuth ships, the following six-step plan picks up from the current state. Don't half-land any of it — when it's time, do all six in one branch:

1. `pip install authlib` (or `google-auth` + `google-auth-oauthlib`); add to `requirements.txt`.
2. New table `user_identities(id, user_id, provider, provider_subject, created_at, UNIQUE(provider, provider_subject))`. On migration, seed one `('password', user.email)` row per existing user for consistency. Also rebuild `users` to drop NOT NULL on `password_hash` (SQLite requires a table rebuild — do it in a separate commit with a pre-flight backup).
3. New endpoints in `api/routers/auth.py`:
   - `GET /auth/oauth/google/start` — PKCE + state, 302 to Google.
   - `GET /auth/oauth/google/callback` — exchange code, verify `id_token`, look up by `(provider='google', provider_subject=sub)`. If not found, look up by email: link if an existing password user matches, else call `_create_user_from_verified_identity(email=…, password_hash=None, verified=True)`. Issue session via `_issue_session`.
4. Frontend: render a "Continue with Google" button in the reserved `.auth-alt` slot (`chat-ui/js/auth.js`); point it at `/auth/oauth/google/start`.
5. Settings modal: add an "Account" section listing linked identities, with unlink buttons. Guard: don't let a user unlink their last identity if they have no password.
6. Google Cloud Console: create OAuth client, set authorized redirect URI to `<prod-url>/auth/oauth/google/callback` (and `http://localhost:8001/auth/oauth/google/callback` for dev).

The tail `_create_user_from_verified_identity` → `_issue_session` path in `api/routers/auth.py` is already shaped so the OAuth callback reuses it unchanged — the password and OAuth flows differ only in how they produce a verified email.

## Notes

- 2025 stats available from nflverse native data
- `season_stats.recent_team` is backfilled from `game_stats` (most common team per player-season)
- Kicker stats are in `game_stats`/`season_stats` (fg_made, fg_att, fg_pct, pat_made, etc.)
- `combine` table has no join edges — query separately
- NGS `stat_type`: `passing`/`rushing`/`receiving`; `week=0` = season totals
- PFR `stat_type`: `pass`/`rush`/`rec` (different naming!)
- QBR `game_week` is an INTEGER (1, 2, ...), **no season total rows** — use `AVG(qbr_total)` grouped by player+season. `season_type` is `"Regular"`/`"Postseason"`
