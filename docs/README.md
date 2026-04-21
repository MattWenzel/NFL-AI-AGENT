# Internal Design Docs

Technical documentation for the AI agent plumbing in this repo — the runtime loop, tool dispatch, compaction, provider adapters, persistence, HTTP transport, auth, and browser UI. These docs cover **how the code works**, not how to run it (see `CLAUDE.md` for deployment/ops) and not what the NFL data means (see `NFLVERSE/docs/DATABASE.md`).

## Reading order

For a new engineer, read in this order:

1. **[architecture.md](architecture.md)** — Top-level map. Layering, data flow of one turn, where to find things.
2. **[runtime.md](runtime.md)** — `ChatRuntime`, the iteration loop, events, doom-loop guard.
3. **[tools.md](tools.md)** — Tool definitions, dispatch, validation, SQL sandbox.
4. **[prompts.md](prompts.md)** — Base system prompt + the on-demand guide system.
5. **[compaction.md](compaction.md)** — How long conversations stay under the context window.
6. **[providers.md](providers.md)** — `BaseLLMClient`, canonical types, Anthropic vs OpenAI adapters.
7. **[persistence.md](persistence.md)** — SQLite schema, session/turn/tool_run lifecycle.
8. **[transport.md](transport.md)** — FastAPI, SSE streaming, IDOR guards.
9. **[auth.md](auth.md)** — Bearer tokens, rate limiting, Fernet-encrypted API keys.
10. **[ui.md](ui.md)** — Browser state machine, SSE consumption, streaming performance.

## Conventions

- Every claim about runtime behavior cites `file.py:line` so docs stay grounded.
- Diagrams are ASCII; no renderer dependency.
- Docs describe the current code. If they diverge, the code wins — open a PR to fix the doc.

## What's out of scope

- **Deployment.** Runbooks for Fly.io and self-hosted VPS live in `CLAUDE.md`.
- **API reference.** Interactive OpenAPI docs are served at `/docs` when the app is running.
- **NFL data semantics.** Table schemas, join graph, play-by-play columns — all in `NFLVERSE/docs/DATABASE.md` and the in-app guides (`tools/guides/`).
