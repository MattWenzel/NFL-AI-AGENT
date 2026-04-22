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
├── store.py                #   RuntimeStore(UsersMixin, TranscriptsMixin, ExportsMixin); owns engines, per-session lock, startup reconcile
├── models.py               #   SQLModel table classes (SessionRecord, TurnRecord, ToolRunRecord, etc.) + helpers (utcnow, new_id, wrap_summaries_for_prompt)
├── engine.py               #   sync engine (bootstrap/migrations) + async engine (aiosqlite) + async_sessionmaker
├── types.py                #   TolerantJSONList / ToolInputJSON TypeDecorators (log-and-recover on malformed rows)
├── users.py                #   UsersMixin (users, auth_sessions, user_api_keys)
├── transcripts.py          #   TranscriptsMixin (sessions, turns, parts, tool runs, compaction)
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
chat.html                   # Browser UI (SSE streaming, provider selection)
tests/                      # pytest test suite
data/                       # Runtime data (runtime.sqlite3 — ignored)
```

## Development

```bash
python3 run.py                    # API server (port 8001)
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
4. Frontend: render a "Continue with Google" button in the reserved `.auth-alt` slot (`chat-ui/js/auth.js`); point it at `/auth/oauth/google/start`.
5. Settings modal: add an "Account" section listing linked identities, with unlink buttons. Guard: don't let a user unlink their last identity if they have no password.
6. Google Cloud Console: create OAuth client, set authorized redirect URI to `<prod-url>/auth/oauth/google/callback` (and `http://localhost:8001/auth/oauth/google/callback` for dev).

The tail `_create_user_from_verified_identity` → `_issue_session` path in `server/routes/auth.py` is already shaped so the OAuth callback reuses it unchanged — the password and OAuth flows differ only in how they produce a verified email.

## Deployment

The app is single-origin: FastAPI serves the UI (`GET /` → `chat.html`, static assets at `/chat-ui/*`) and the API. One process, one domain. **TLS is mandatory** — passwords, bearer tokens, and user API keys all move over the wire; without HTTPS they leak.

Two documented paths: **Fly.io** (recommended, minimal ops overhead, TLS + volumes built-in) and **self-hosted VPS with Caddy** (more DIY, more control).

### Deploying to Fly.io

Repo ships with `Dockerfile`, `fly.toml`, `.dockerignore`, and `.python-version` tuned for this deploy. Config paths (`DB_PATH`, `PBP_DB_PATH`, `RUNTIME_DB_PATH`, `EXPORTS_DIR`) are env-driven and point at `/data/...` on the Fly volume in `fly.toml`.

**Cost estimate:** `shared-cpu-1x@2gb` + 10GB volume ≈ $12-13/mo.

#### Pre-flight

- [ ] Install flyctl: `curl -L https://fly.io/install.sh | sh` (or `brew install flyctl` on macOS).
- [ ] `fly auth login` — browser-interactive.
- [ ] `fly auth whoami` confirms you're in.

#### One-time setup

```bash
# Pick a unique app name (globally unique on Fly); edit fly.toml's `app = ...`
# if the default collides. Primary region is already set to `iad` in fly.toml.
fly apps create <your-app-name>

# 10GB volume: ~2.3GB for the NFL DBs, plenty of room for the runtime DB and
# CSV exports to grow.
fly volumes create nfl_data --region iad --size 10 --yes

# Secrets — env vars that aren't in fly.toml for security. Generate the
# encryption key with the Fernet one-liner below if you don't already have one.
fly secrets set \
    SETTINGS_ENCRYPTION_KEY='<your Fernet key>' \
    REGISTRATION_INVITE_CODE='<a secret code>'

# Deploy — builds the Docker image, pushes to Fly's registry, starts a machine
# with the volume attached. Healthcheck on /health must pass for the deploy
# to succeed.
fly deploy
```

Generate a Fernet key locally if needed:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

#### Seed the databases

The 2.3GB of nflverse + pbp DBs aren't in the Docker image (they'd bloat every deploy); they live on the volume. The Dockerfile's CMD creates `/data/runtime`, `/data/nflverse`, and `/data/exports` on every startup, so a fresh volume is ready for uploads without any prep. Push the DBs via SFTP (two separate one-shot commands — less fragile than the interactive shell):

```bash
fly ssh sftp put NFLVERSE/data/nflverse.db /data/nflverse/nflverse.db
fly ssh sftp put NFLVERSE/data/pbp.db /data/nflverse/pbp.db
```

The upload goes through Fly's ssh proxy at your home upload speed — 2GB typically takes 20-60 minutes. Run the `pbp.db` one in the background (`&` or a separate terminal) and the machine stays running through it.

After both uploads finish, restart the machine so the app reopens SQLite handles against the freshly-seeded files:

```bash
fly machine list     # grab the machine ID
fly machine restart <machine-id>
```

#### Verify

```bash
curl https://<your-app-name>.fly.dev/health
# {"status":"ok"}

curl https://<your-app-name>.fly.dev/auth/status
# {"has_users":false,"authenticated":false,"user":null,"invite_required":true}

# Row-count sanity. Wrap in `sh -c '...'` because flyctl's -C parses remaining
# args as flags for the outer command, not as args to sqlite3.
fly ssh console -C "sh -c 'sqlite3 /data/nflverse/nflverse.db \"SELECT COUNT(*) FROM players;\" && sqlite3 /data/nflverse/pbp.db \"SELECT COUNT(*) FROM play_by_play;\"'"
# Should print two counts matching your local copies.
```

Visit `https://<your-app-name>.fly.dev/` in a browser, register with your invite code, paste an API key into Settings, ask a question. If all that works you're live.

#### Ongoing ops

- **Logs:** `fly logs` (tail) or `fly logs --since 1h`.
- **Shell:** `fly ssh console`.
- **Deploy code changes:** `git push` and `fly deploy`. Volume and secrets persist across deploys; only the app container is replaced.
- **Backups:** `fly ssh console -C "sh -c 'sqlite3 /data/runtime/runtime.sqlite3 \".backup /data/runtime/backup-$(date +%F).sqlite3\"'"` — or pull a copy locally with `fly ssh sftp get /data/runtime/runtime.sqlite3 ./runtime-backup.sqlite3` periodically. The runtime DB holds users, conversations, and encrypted API keys; the nflverse DBs are reproducible.
- **Rotate the encryption key or invite code:** `fly secrets set KEY=new_value` → Fly restarts the machine automatically. **Do not rotate `SETTINGS_ENCRYPTION_KEY` without a migration plan** — every stored user API key becomes undecryptable the moment the old key is gone.
- **Scale memory:** `fly scale memory 4096` if pbp queries start hitting OOM.

#### Custom domain (optional)

`fly certs create yourhost.com` then add the DNS records Fly prints. TLS is auto-provisioned in seconds.

### Alternative: self-hosted VPS (Caddy + systemd)

More control, more ops work. TLS still mandatory — don't skip it.

#### Pre-flight

- [ ] `SETTINGS_ENCRYPTION_KEY` set in the server's env. Losing the key makes every stored API key unreadable.
- [ ] App bound to `127.0.0.1`, not `0.0.0.0`. Verify with `ss -tlnp | grep 8001`.
- [ ] Caddy (or nginx) terminates TLS. `curl -I https://yourhost.com` returns 200 with a valid cert.
- [ ] Firewall blocks inbound 8001 from the internet. Only 80/443 open.
- [ ] Backup job for `data/runtime.sqlite3` verified (restore into a scratch DB to confirm).

#### Setup

```bash
git clone <repo> /srv/nflverse && cd /srv/nflverse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Place the DBs (nflverse.db + pbp.db) under NFLVERSE/data/ — see build scripts above

python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`/srv/nflverse/.env`:

```
SETTINGS_ENCRYPTION_KEY=<output from the command above>
HOST=127.0.0.1                            # loopback — reachable only via reverse proxy
FORWARDED_ALLOW_IPS=127.0.0.1             # trust X-Forwarded-For only from local proxy
ALLOWED_ORIGINS=https://yourhost.com      # optional
AUTH_TOKEN_TTL_DAYS=30                    # optional; default 30
REGISTRATION_INVITE_CODE=<a secret>       # optional; open signup if unset
```

#### Run

```bash
uvicorn server.app:app \
    --host 127.0.0.1 --port 8001 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
```

- No `--reload`, no `--workers N > 1` (rate limiter is in-memory per-process).

#### Caddy

`/etc/caddy/Caddyfile`:

```
yourhost.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8001
}
```

`systemctl reload caddy` and Caddy provisions the cert on first HTTPS request, sets `X-Forwarded-*` headers, and adds HSTS automatically.

#### systemd unit

`/etc/systemd/system/nflverse.service`:

```ini
[Unit]
Description=nflverse API + UI
After=network.target

[Service]
User=nflverse
WorkingDirectory=/srv/nflverse
EnvironmentFile=/srv/nflverse/.env
ExecStart=/srv/nflverse/.venv/bin/uvicorn server.app:app \
    --host 127.0.0.1 --port 8001 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now nflverse
journalctl -u nflverse -f   # tail logs
```

#### Backups

`data/runtime.sqlite3` holds everything mutable. Nightly `sqlite3 data/runtime.sqlite3 '.backup /backups/runtime-$(date +%F).sqlite3'` in cron is the whole story. Keep `.env` backups separate from DB backups — an attacker with both can decrypt stored keys.

### Dev path

`python3 run.py` → open `http://localhost:8001/`. Defaults bind to `127.0.0.1`; set `HOST=0.0.0.0 python3 run.py` to expose to LAN.

## Notes

- 2025 stats available from nflverse native data
- `season_stats.recent_team` is backfilled from `game_stats` (most common team per player-season)
- Kicker stats are in `game_stats`/`season_stats` (fg_made, fg_att, fg_pct, pat_made, etc.)
- `combine` table has no join edges — query separately
- NGS `stat_type`: `passing`/`rushing`/`receiving`; `week=0` = season totals
- PFR `stat_type`: `pass`/`rush`/`rec` (different naming!)
- QBR `game_week` is an INTEGER (1, 2, ...), **no season total rows** — use `AVG(qbr_total)` grouped by player+season. `season_type` is `"Regular"`/`"Postseason"`
