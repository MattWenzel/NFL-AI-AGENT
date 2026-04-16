# nflverse DB

NFL player stats database built from [nflverse](https://github.com/nflverse/nflverse-data) data.

## Quick Reference

| Database | Size | Tables | Rows | Years |
|----------|------|--------|------|-------|
| `nflverse.db` | ~200 MB | 13 | 1.75M | 1999-2025 |
| `pbp.db` | ~550 MB | 1 | 1.28M | 1999-2025 |

**Full schema**: [docs/DATABASE.md](docs/DATABASE.md)
**API reference**: [docs/API.md](docs/API.md)
**Audit log**: [docs/AUDIT_LOG.md](docs/AUDIT_LOG.md) — past audit findings (fixed + false positives); check before re-auditing

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

Base URL: `http://localhost:8001` | Interactive docs: `/docs` | Read-only database.

See [docs/API.md](docs/API.md) for full endpoint reference. Key access patterns:

- **`POST /chat/stream`** — AI chat with streaming
- **`GET /exports/{filename}`** — CSV export download

## AI Chat Agent

Natural language interface to the database. Supports multiple LLM providers (Anthropic Claude, OpenAI GPT, ChatGPT Codex via OAuth) with tool_use to translate questions into SQL queries.

### LLM Providers

| Provider | Auth | Default Model | Context |
|----------|------|---------------|---------|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | 200K |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` | 128K |
| Codex | OAuth (PKCE) → `data/codex_auth.json` | `gpt-5.1-codex` | 200K |

Select via `CHAT_PROVIDER` env var (default: `anthropic`), CLI `--provider` flag, or UI dropdown. Codex requires a one-time sign-in via the UI's **Sign in with ChatGPT** button or `python3 chat_cli.py login --provider codex`.

**Provider abstraction**: [docs/PROVIDERS.md](docs/PROVIDERS.md) — canonical types (`StopReason`, `Usage`, `ToolDefinition`), `BaseLLMClient` ABC, registry/factory, per-provider implementation details, and how to add new providers.

The chat runtime is transcript-backed: sessions, turns, assistant parts, tool runs, and compaction summaries are persisted in `data/runtime.sqlite3`. Long conversations are compacted by summarizing older turns and excluding older raw tool output from active prompt context while keeping the full transcript in storage.

### Architecture

```
agent/
├── providers/
│   ├── __init__.py        # Registry, factory (create_client), ProviderInfo (+auth_type, provider_is_available)
│   ├── base.py            # Canonical types + ABC + _wrap_api_errors/_translate_error
│   ├── anthropic_provider.py  # Anthropic Claude
│   ├── openai_provider.py     # OpenAI GPT (optional SDK)
│   └── codex_provider.py      # ChatGPT Codex via OAuth + raw httpx + custom SSE
├── oauth/                 # PKCE, JWT decode, token store, loopback capture, orchestrator
│   ├── pkce.py
│   ├── jwt_decode.py
│   ├── token_store.py
│   ├── codex_auth.py
│   └── login_server.py
├── runtime.py         # Shared persisted runtime loop + normalized runtime events
├── runtime_store.py   # SQLite transcript store (sessions, turns, parts, tool runs, compaction)
├── tools.py           # 5 tools + TOOLS (typed) / TOOL_DEFINITIONS (raw dicts)
├── system_prompt.py   # Condensed DB knowledge (~5K tokens)
├── provider_hints.py  # Per-provider supplemental hints + get_system_prompt(provider)
├── sql_sandbox.py     # Read-only SQL with timeout, row limit, PBP auto-attach
config.py              # DB paths, runtime db path, CODEX_AUTH_PATH, load_dotenv()
chat_cli.py            # CLI entry point (--provider, --model flags; `login` subcommand)
chat.html              # Browser UI (SSE streaming, provider selection, Sign in with ChatGPT)
api/routers/chat.py    # FastAPI chat endpoints + transcript retrieval
api/routers/auth.py    # /auth/codex/{login,status,logout} for the UI sign-in flow
api/routers/exports.py # CSV export file serving + auto-cleanup
tests/                 # pytest test suite
data/                  # Runtime data (runtime.sqlite3, codex_auth.json — ignored)
backups/               # Old database files (pre-v2)
```

## Development

```bash
python3 run.py              # API server (port 8001)
python3 chat_cli.py         # AI chat agent (default: Anthropic)
python3 chat_cli.py -p openai   # Use OpenAI
python3 chat_cli.py login --provider codex   # OAuth sign-in for Codex
python3 chat_cli.py -p codex    # Use Codex (after sign-in)
open chat.html              # Chat UI
python3 -m pytest tests/    # Run tests

# Build scripts (in NFLVERSE/)
python3 NFLVERSE/scripts/download.py                      # Fetch raw parquet into data/raw/
python3 NFLVERSE/scripts/build_db.py --all                # Core DB (from local parquet)
python3 NFLVERSE/scripts/build_db.py --pbp --all          # Play-by-play (from local parquet)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --all      # Fallback: core DB via nflreadpy (network)
python3 NFLVERSE/scripts/build_db_nflreadpy.py --pbp --all # Fallback: PBP via nflreadpy (network)
python3 NFLVERSE/scripts/check_updates.py                 # Check which tables/years are stale
```

**Note**: Restart the API server (`python3 run.py`) after changing `system_prompt.py` or `tools.py` — the running server caches imports.

## Notes

- 2025 stats available from nflverse native data
- `season_stats.recent_team` is backfilled from `game_stats` (most common team per player-season)
- Kicker stats are in `game_stats`/`season_stats` (fg_made, fg_att, fg_pct, pat_made, etc.)
- `combine` table has no join edges — query separately
- NGS `stat_type`: `passing`/`rushing`/`receiving`; `week=0` = season totals
- PFR `stat_type`: `pass`/`rush`/`rec` (different naming!)
- QBR `game_week` is an INTEGER (1, 2, ...), **no season total rows** — use `AVG(qbr_total)` grouped by player+season. `season_type` is `"Regular"`/`"Postseason"`
