# Internal Design Docs

Technical documentation for the AI agent plumbing in this repo — the runtime loop, tool dispatch, compaction, provider adapters, persistence, HTTP transport, auth, and browser UI. These docs cover **how the code works**; for what the NFL data means see `NFLVERSE/docs/DATABASE.md`.

## Reading order

For a new engineer, read in this order:

1. **[architecture.md](architecture.md)** — Top-level map. Layering, data flow of one turn, where to find things.
2. **[runtime.md](runtime.md)** — `ChatRuntime`, the iteration loop, events, doom-loop guard, plus the stateless helper loop used by the Database tab.
3. **[tools.md](tools.md)** — Tool definitions (10 tools), dispatch, validation, DuckDB SQL sandbox, the `ctx` side-channel.
4. **[prompts.md](prompts.md)** — Base system prompt + the on-demand guide system + the Reports/helper variants.
5. **[compaction.md](compaction.md)** — How long conversations stay under the context window.
6. **[providers.md](providers.md)** — `BaseLLMClient`, canonical types, Anthropic / OpenAI / OpenAI-Codex adapters.
7. **[persistence.md](persistence.md)** — Runtime SQLite schema (sessions, turns, parts, tool_runs, table_states for Reports, exports, users), session/turn/tool_run lifecycle.
8. **[transport.md](transport.md)** — FastAPI, SSE streaming, IDOR guards, CSRF.
9. **[auth.md](auth.md)** — Password / Google OAuth / Sign-in-with-ChatGPT, bearer tokens + browser session cookies, rate limiting, Fernet-encrypted API keys.
10. **[ui.md](ui.md)** — React + Vite browser app, `chatStore`, SSE consumption, AppShell.
11. **[database-browser.md](database-browser.md)** — The Database tab + ephemeral SQL helper chat.

## Conventions

- Every claim about runtime behavior cites `file.py:line` so docs stay grounded.
- Diagrams are ASCII; no renderer dependency.
- Docs describe the current code. If they diverge, the code wins — open a PR to fix the doc.

## What's out of scope

- **API reference.** Interactive OpenAPI docs are served at `/docs` when the app is running.
- **NFL data semantics.** Table schemas, join graph, play-by-play columns — all in `NFLVERSE/docs/DATABASE.md` and the in-app guides (`backend/domain/tools/guides/`).
