# NFL AI Agent

A natural-language interface to 27 years of NFL statistics. Ask questions in plain English; the agent translates them into SQL, queries a local SQLite database, and answers with tables and context.

Powered by Claude (Anthropic) or GPT (OpenAI) with tool use. Runs entirely on your machine — no hosted backend.

## What you can ask

- "Top 10 QBs by EPA per play in 2024, minimum 200 attempts"
- "Show me Justin Jefferson's weekly snap share last season"
- "Which kickers made the most 50+ yard field goals since 2015?"
- "Red-zone passing TD rate for Mahomes vs. Allen, 2023 and 2024"
- "Export the 2024 RB rushing leaderboard as a CSV"

## Architecture

Top-level folders are organized by subsystem:

- **`agent/`** — LLM conversation domain: runtime loop, compaction, prompts
- **`tools/`** — Tool registry + handlers (SQL sandbox, schema discovery, CSV export, etc.)
- **`auth/`** — Auth subsystem: password primitives, encryption, Codex OAuth, credential refresh
- **`provider/`** — LLM adapters (Anthropic, OpenAI, OpenAI Codex)
- **`storage/`** — SQLite persistence (`RuntimeStore` facade composed of per-domain mixins)
- **`server/`** — FastAPI HTTP layer (app factory, routes, dependencies, schemas)
- **`web/`** — browser UI (`index.html` + static assets, SSE streaming, provider picker)

For a deeper walkthrough see [CLAUDE.md](CLAUDE.md).

## Setup

### 1. Build the databases

The agent reads from two SQLite files that live under `NFLVERSE/data/`. You build them with the separate [NFLVERSE-DB](https://github.com/MattWenzel/NFLVERSE-DB) repo, cloned as a sibling into `NFLVERSE/`:

```bash
git clone https://github.com/MattWenzel/NFLVERSE-DB.git NFLVERSE
cd NFLVERSE
pip install -r requirements.txt
python3 scripts/download.py --all
python3 scripts/build_db.py --all                          # ~327 MB, core tables
python3 scripts/download.py --tables play_by_play --all    # Optional, ~466 MB download
python3 scripts/build_db.py --pbp --all                    # Optional, ~2 GB, play-by-play
cd ..
```

After this you should have `NFLVERSE/data/nflverse.db` and (optionally) `NFLVERSE/data/pbp.db`. See the [NFLVERSE-DB README](https://github.com/MattWenzel/NFLVERSE-DB) for incremental updates and fallback build paths.

### 2. Install Python deps

```bash
pip install -r requirements.txt
```

### 3. Configure an LLM provider

Create a `.env` file in the project root. `SETTINGS_ENCRYPTION_KEY` is
required for startup; generate it once and keep it stable so stored API
keys remain decryptable.

```
SETTINGS_ENCRYPTION_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
CHAT_PROVIDER=anthropic     # optional — picks the default provider
```

Generate an encryption key with:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Run

```bash
python3 run.py               # API server + UI on http://localhost:8001
```

## Providers

| Provider  | Env var              | Default model               | Context |
|-----------|----------------------|-----------------------------|---------|
| Anthropic | `ANTHROPIC_API_KEY`  | `claude-sonnet-4-6`         | 200K    |
| OpenAI    | `OPENAI_API_KEY`     | `gpt-5`                     | 128K    |

Adding a provider is a new file under `provider/` plus one `register_provider()` call — see `provider/base.py` for the ABC.

## Notes

- **Local-only by design.** `run.py` binds to `127.0.0.1`; CORS is restricted to `localhost` origins. Don't expose the API to the internet without reworking auth — the SQL sandbox is read-only and sandboxed, but the chat interface is open.
- **The agent can write SQL** via its `execute_sql` tool, but queries are parsed, rejected if they contain DDL/DML, row-limited, and run against a read-only SQLite connection.
- **Conversation transcripts** persist in `data/runtime.sqlite3` so you can reopen a chat, review tool runs, or download transcripts via `GET /chat/conversations/{id}/transcript`.

## License

[MIT](LICENSE) — see `LICENSE` for the full text.

NFL data itself is licensed CC-BY-4.0 by [nflverse](https://github.com/nflverse/nflverse-data); see the NFLVERSE-DB README for attribution details.
