# Database browser

A dedicated tab in the React app for poking around `nflverse.duckdb` directly: a schema browser on the left, a SQL editor + result table in the middle, and an ephemeral SQL helper chat on the right. It's distinct from regular Chat (persistent agent conversations) and from Reports (pinned table results that the agent can rewrite); the Database tab is for *exploring the data* without committing to a conversation.

## Why it's its own surface

A regular chat session is the wrong shape for "what tables have receiving stats?" or "is this query right?" — those are planning aids, not artifacts worth saving. Burying the question inside a chat means the user ends up with an open conversation they don't actually want, and the side-channel of "now look at the schema and tell me about column X" pulls the agent out of its main job. Splitting the schema-research conversation off, making it stateless, and giving it its own UI:

- Keeps the regular agent's transcript focused on the user's actual analyses.
- Lets refresh be the natural reset (no clutter accumulates in the sidebar).
- Lets the helper have access to a tool the regular agent doesn't (`run_in_editor`) without polluting the main agent's tool surface.

## UI surfaces

The Database tab (`frontend/src/components/database/DatabaseView.tsx`) has three panels:

- **Sidebar** — browseable list of every table in `nflverse.duckdb` with column-level search (`frontend/src/components/sidebar/Sidebar.tsx`). Searching by column name surfaces the matching tables and shows the matched columns inline. Clicking a table makes it the active table.
- **Main pane** — collapsible/copyable SQL editor at the top, result table below. Selecting a table auto-runs `SELECT * FROM <table> LIMIT 100`. Toolbar actions: **Run**, **Save as Report** (spawns a `kind="table_chat"` session seeded with the rows), and a **SQL helper** button (only visible when the helper panel is closed).
- **Helper chat (right pane)** — slides in via `AppShell`'s `alternateInspector` prop. Owns its own composer with provider/model/tool-choice popover, a stripped-down message renderer, and a Trash icon to clear. Click outside the panel to dismiss; click the editor / table cells to dismiss too (it's a side conversation, not a drill-down).

## Backend endpoints

All under the `/database` router (`backend/api/routes/database.py`), all CSRF-protected, all auth-gated:

| Method + path | Purpose |
|---|---|
| `GET /database/tables` | List every table in the DuckDB with row counts. Cached at process start. |
| `POST /database/query` | Run a single read-only `SELECT`/`WITH` against the DuckDB. 500-row, ~30 s cap (same as `execute_sql`). |
| `POST /database/save-as-report` | Persist a query result as a new Report (`kind="table_chat"` session linked to the issuing user). |
| `POST /database/helper-chat/stream` | Stream a turn of the helper-chat agent loop. Body carries the full message history; nothing is persisted server-side. |

## The stateless agent loop

The helper chat does not use `ChatRuntime`. Instead it uses a separate generator in `backend/domain/agent/stateless.py`:

```python
async def run_stateless_turn(
    *,
    messages: list[Message],
    client: BaseLLMClient,
    tools: list[ToolDefinition],
    system: str,
    tool_choice: ToolChoice | None = None,
    max_iterations: int = MAX_HELPER_ITERATIONS,  # = 8
) -> AsyncGenerator[RuntimeEvent, None]: ...
```

Each call drives the model through up to 8 iterations of `stream_message → tool dispatch → tool result → stream_message → …` and yields the same `RuntimeEvent` types (`TextDeltaEvent`, `ToolPendingEvent`, `ToolCompletedEvent`, `ToolFailedEvent`, `RetryingEvent`, `RuntimeErrorEvent`) the regular runtime emits — so the existing SSE serializer needs only a small wrapper to inline tool result content (the browser has no `RuntimeStore` to fetch from after the fact).

The synthetic `_HELPER_SESSION_ID = "db-helper"` / `_HELPER_TURN_ID = "db-helper-turn"` are used purely for event-ID stability inside one turn. Nothing keys off them in storage; nothing is written to storage at all.

`backend/application/db_helper_chat.py::DbHelperChatService.stream` is the wrapper the route calls: resolves the user's provider + credentials (same path as `ChatService.prepare_chat`), builds the wire `Message` list via `build_messages_from_raw`, picks the helper-only tool subset, and yields `run_stateless_turn(...)`.

## Tool whitelist

`ALLOWED_HELPER_TOOLS` in `stateless.py`:

```
frozenset({
    "get_schema", "get_guide", "execute_sql",
    "search_players", "get_player_info",
    "run_in_editor",
})
```

A model that emits a tool call outside this set (`set_table`, `create_report`, `create_csv_export`, `create_chart`) gets a `ToolFailedEvent` with an "X is not available in the DB helper" message and no dispatch occurs. This is defense-in-depth: the helper's system prompt also doesn't mention those tools, but the whitelist is what guarantees no `set_table` call can sneak through and mutate a Report the user happens to have open.

`run_in_editor` is the inverse case — it's *only* in the helper's whitelist, never offered to the regular agent. It's a remote-control affordance that only makes sense inside the Database tab.

## `run_in_editor` mechanics

When the user says "run that" or "do it" (vs. "give me the SQL"), the helper agent calls `run_in_editor({sql: "..."})`. The handler in `backend/domain/tools/handlers/run_in_editor.py` does no I/O — it just `validate_sql`s the SQL and returns a JSON envelope:

```json
{"status": "queued", "sql": "...", "message": "SQL placed in the user's editor and will run automatically."}
```

The streaming hook (`frontend/src/lib/dbHelperChat.ts::useDbHelperChat`) parses every `tool_result` event for `name === "run_in_editor"`, extracts the SQL, and invokes the `onRunInEditor(sql)` callback. `App.tsx` wires that callback to a ref forwarded into `DatabaseView` (`useImperativeHandle`-exposed `runQuery(sql)`), which writes the SQL to the editor's localStorage-backed state and triggers the same code path the manual Run button uses.

The result: the user sees "OK, running that for you" in the chat, the editor populates with the SQL, and the result table updates — all without the helper ever touching the database itself.

## What it deliberately doesn't do

- **No persistence.** Every turn resends the entire message history; the server writes nothing. Refresh wipes the conversation. This is by design: the helper is a planning aid, not an artifact.
- **No compaction / token-window management.** Helper conversations are short by user behavior, and refresh is the natural compaction mechanism. If they ever start getting long enough to matter, sliding-window cropping is the obvious next step — not summarization (which requires persistence).
- **No multi-user state.** No locks, no concurrent-streamer tracking, no rate-limiter slot. The user's session is the only handle.
- **No Report creation from the helper.** The Database tab's toolbar has its own **Save as Report** button (`POST /database/save-as-report`); the helper doesn't have `create_report` in its whitelist. Keeps the model from "helping" by spawning Reports the user didn't ask for.

## Critical files

| File | Role |
|---|---|
| `backend/domain/agent/stateless.py` | The agent loop. `MAX_HELPER_ITERATIONS`, `ALLOWED_HELPER_TOOLS`, `run_stateless_turn`. |
| `backend/application/db_helper_chat.py` | `DbHelperChatService` — wires provider creds + tools + the system prompt to `run_stateless_turn`. |
| `backend/api/routes/database.py` | All four `/database/*` endpoints. |
| `backend/domain/agent/system_prompt.py::get_db_helper_prompt` | The focused system prompt — explicitly tells the model when to use `run_in_editor` vs. responding with a fenced SQL block. |
| `backend/domain/tools/handlers/run_in_editor.py` | The tool handler. Validates SQL, returns the queued-SQL envelope. |
| `frontend/src/lib/dbHelperChat.ts` | `useDbHelperChat` hook — owns local message state, opens the SSE stream, dispatches `run_in_editor` to the parent. |
| `frontend/src/components/database/DbHelperChat.tsx` | The chat panel rendered into `AppShell`'s `alternateInspector` slot. |
| `frontend/src/components/database/HelperComposer.tsx` | Slim composer (textarea + provider/model/tool-choice popover + send/stop), reads/writes the same localStorage keys as the main `Composer`. |
| `frontend/src/components/database/HelperMessageList.tsx` | User bubble + assistant bubble + collapsible `<details>` per tool call. |
| `frontend/src/components/database/DatabaseView.tsx` | The main pane. Exposes `runQuery(sql)` via `useImperativeHandle` for the helper to drive. |

## Tests

- `tests/test_stateless_runtime.py` — exercises `run_stateless_turn` directly with a stub provider client: text-only turn terminates after one iteration, tool call → followup text, error tools yield `ToolFailedEvent`, max-iterations cap fires, non-whitelisted tools rejected without dispatch, provider errors surface as `RuntimeErrorEvent`.
- `tests/test_db_helper_chat_routes.py` — the HTTP endpoint: auth-gated, CSRF-required, happy path streaming text + tool calls, configuration errors emit SSE `error` events, and most importantly: zero rows written to `sessions` after a full helper conversation (the "no persistence" invariant is asserted, not just implied).
