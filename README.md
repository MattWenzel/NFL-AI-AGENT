# NFL AI Agent

A natural-language interface to 27 years of NFL statistics. Ask questions in plain English; the agent writes SQL against a database of 1999–2025 stats and answers with tables, charts, and full tool-call transparency.

**Live at [nfl-stats-agent.fly.dev](https://nfl-stats-agent.fly.dev).**

Bring your own Anthropic or OpenAI key, or sign in with ChatGPT — no key required.

## What you can ask

- "Top 10 QBs by EPA per play in 2024, minimum 200 attempts"
- "Show me Justin Jefferson's weekly snap share last season"
- "Which kickers made the most 50+ yard field goals since 2015?"
- "Red-zone passing TD rate for Mahomes vs. Allen, 2023 and 2024"
- "Export the 2024 RB rushing leaderboard as a CSV"

Anything that resolves to a SQL query against player, game, snap-count, NGS, QBR, PFR advanced, play-by-play, draft, or combine data is fair game.

## Features

### Full NFL dataset, 1999–2025

Every week, every snap. The agent has access to:

- **Core stats** — players, games, weekly + season game logs, kicking, rushing, receiving, passing, defense.
- **Play-by-play** — 1.28 million plays with EPA, WPA, success rate, air yards, pressure, pass location, drive context — everything nflverse ships.
- **Supplementary** — snap counts, Next Gen Stats (passing/rushing/receiving), PFR Advanced, ESPN QBR, depth charts, draft picks, NFL Combine.

Full schema in `../NFLVERSE/docs/DATABASE.md` — the sibling repo that owns the data pipeline and produces `nflverse.duckdb`. This app consumes it read-only via `DB_PATH` in `.env`.

### Bring your own model

Three provider paths; pick per turn from the dropdown:

| Provider | Credential | Models |
|----------|-----------|--------|
| **Anthropic** | API key | Claude Sonnet 4.6, Opus 4.7, Haiku 4.5 |
| **OpenAI** | API key | GPT-5, GPT-5-mini, o3, o3-mini, GPT-4.1, GPT-4o |
| **ChatGPT (OAuth)** | Sign in with ChatGPT | GPT-5.3-codex |

The **ChatGPT OAuth** path is the one that doesn't need an API key. Settings → Connect ChatGPT kicks off a device-code flow; sign in with your existing ChatGPT account and use Codex-backed models through your subscription. Same auth dance as OpenAI's official Codex CLI.

API keys are Fernet-encrypted at rest; OAuth refresh tokens are managed server-side with per-user locks so concurrent requests don't thrash the refresh endpoint.

### Response control

Two knobs per turn beyond provider + model:

- **Tool choice** — `auto` (the model decides), `required` (force a tool call), or `none` (text-only). Useful when you want a one-shot query regardless of what the model thinks, or when you want conversational analysis of results already on screen.
- **Model switcher** — swap Sonnet → Opus mid-conversation if the question gets harder; history carries over.

### Transparent runtime

Click any assistant reply and the inspector panel opens with the full runtime transcript:

- Every tool call — full input JSON, full result, duration, status, error + remediation hint if it failed.
- Compaction events showing exactly what got summarized.
- Token usage per turn.
- The "Thinking…" collapsible per assistant turn surfaces tool calls inline as they run, with status chips flipping as each resolves.

No black box. If the agent got the wrong answer, you can see which query it ran and fix it.

### Output formats

- **Tables** — markdown, rendered with proper alignment and thousands separators.
- **Charts** — the agent can call `create_chart` to render interactive bar, line, scatter, or pie charts inline via Chart.js.
- **CSV exports** — up to 10,000 rows per export, wider time/row budget than the in-chat query (500 rows / ~30 s). Every export lands in your CSV Library tab with preview, rename, delete, and "seed a new conversation from this data" actions.

### Saved history

- Conversations persist across sessions. Pin the important ones to the top of the sidebar.
- Long conversations auto-compact: older turns are summarized into a memo and dropped from the active prompt while the full transcript stays in storage. You can keep asking follow-ups indefinitely.
- Resume mid-session — the inspector shows what the agent was doing last time.

### Streaming end-to-end

Responses stream token-by-token over SSE. Tool calls start and finish mid-response; you see what the agent is doing as it's doing it, not at the end. A 15-second SSE keepalive plus the right reverse-proxy buffering config means long-running tool calls don't drop the connection.

## Security & privacy

TLS-only deployment; all traffic over HTTPS. Authentication is password + opaque bearer token (no JWT — tokens are revocable server-side), 30-day sessions, rate-limited sign-in, optional invite-code gate on registration. Per-user data is scoped by `user_id` on every query — users can't see each other's conversations, API keys, or CSV exports.

API keys are encrypted with Fernet (AES-128-CBC + HMAC) using a master key held only by the server. The browser never sees ciphertext; from the UI's perspective, Settings is a write-only blob.

The SQL sandbox the model talks to is driver-level read-only (`file:…?mode=ro`), regex-filtered to `SELECT` / `WITH` only, capped at 500 rows and ~30 seconds per query, and runs against two reference databases that never mutate at runtime — nothing the model does can change state for you or any other user.

## For developers

Zero-framework browser UI, FastAPI backend, async SQLModel + aiosqlite persistence, three pluggable LLM adapters, seven tool handlers. Top-level folders:

```
backend/server/       FastAPI app shell, dependency wiring, HTTP helpers, and routes
backend/features/     App-process services, schemas, DTOs, and errors
backend/agent/        Chat runtime loop, turn state, events, prompts, compaction
backend/providers/    LLM provider registry, shared provider types, concrete clients
backend/tools/        Tool definitions, handlers, SQL sandbox, guide docs
backend/storage/      Runtime SQLite store, models, migrations
backend/credentials/  Auth/security primitives, encryption, OAuth protocol helpers
frontend/             Browser app (vanilla JS modules, one render() + one patchLiveText fast path)
```

Full internal-design docs live under [docs/](docs/) — start with [docs/architecture.md](docs/architecture.md). Deployment runbooks (Fly.io + self-hosted VPS) are in [docs/deployment.md](docs/deployment.md); running locally for development is covered there too.

## License

[MIT](LICENSE) — the code.

NFL data itself is licensed CC-BY-4.0 by [nflverse](https://github.com/nflverse/nflverse-data); see the NFLVERSE-DB README for attribution details.
