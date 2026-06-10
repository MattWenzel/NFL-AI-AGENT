# Tools

Tools are how the agent does anything non-verbal. Every SQL query, schema lookup, guide load, CSV export, Report creation, and editor remote-control is a tool call from the model. This doc covers the tool catalog, the dispatch pipeline, the DuckDB SQL sandbox, and the `ctx` side-channel.

The runtime's role in tool calls (concurrent dispatch under `asyncio.gather`, result persistence) is covered in [runtime.md](runtime.md#tool-execution-toolexecutionservice). The Database tab's helper chat uses an entirely separate, stateless agent loop with a tool whitelist subset — see [database-browser.md](database-browser.md).

## File map

Each tool lives in its own module at the top of `backend/domain/tools/`, exporting `TOOL = Tool(...)` — schema and handler on one object:

- `backend/domain/tools/__init__.py` — public surface: `TOOLS`, `execute_tool`, `execute_tool_structured`.
- `backend/domain/tools/registry.py` — assembles the `TOOLS` catalog from the tool modules; owns dispatch (`execute_tool`, `execute_tool_structured`) and error-hint appending.
- Tool modules, one per tool: `search_players.py`, `get_player_info.py`, `execute_sql.py`, `get_schema.py`, `get_guide.py`, `create_chart.py`, `create_csv_export.py`, `create_report.py`, `set_table.py`, `run_in_editor.py`.
- `backend/domain/tools/sandbox/runner.py` — DuckDB read-only SQL runner with row caps and wall-clock timeout.
- `backend/domain/tools/truncate.py` — shared `truncate_text` / `truncate_rows` helpers for result formatting.
- `backend/domain/tools/sandbox/schema_metadata.py` — `TABLE_ALIASES` (hand-coded) and `JOIN_EDGES` (auto-derived from DuckDB's `duckdb_constraints()` at import time; one hand-coded supplement for the `v_depth_charts` view which can't carry an FK). Used by `get_schema` for join-graph hints.
- `backend/domain/tools/guide_registry.py` — `GUIDE_TOPICS` tuple + `GUIDE_INDEX_ROWS` shown in the system prompt's guide index.
- `backend/domain/tools/guides/*.md` — markdown guides loaded by `get_guide`.

The `Tool` dataclass itself lives in `backend/domain/providers/types.py` — providers consume the schema via `to_dict()` (which never serializes the handler); the registry calls `handler`. One type for both halves means a definitions/dispatch rename can't drift: there is no second table to fall out of sync with.

## The ten tools

| Tool | Module | Purpose |
|------|--------|---------|
| `search_players` | `search_players.py` | Fuzzy name/position/team lookup; returns candidates with `gsis_id`. |
| `get_player_info` | `get_player_info.py` | Detailed bio + cross-platform IDs for a given `gsis_id`. |
| `get_guide` | `get_guide.py` | Load a topic-specific markdown guide (fantasy, play_by_play, …). |
| `get_schema` | `get_schema.py` | Table columns + join edges; loaded on demand to save prompt tokens. |
| `execute_sql` | `execute_sql.py` | Arbitrary `SELECT`/`WITH` against `nflverse.duckdb` (500 rows, ~30 s). |
| `create_csv_export` | `create_csv_export.py` | Export query results to a downloadable CSV (10k rows, ~60 s). |
| `create_chart` | `create_chart.py` | Render an inline chart spec (bar/line/scatter/pie) from a query. |
| `set_table` | `set_table.py` | Replace the live table in a Report (table_chat) with the rows from a new SQL query. Rejected when the table is locked. Uses `ctx["persist_table"]`. |
| `create_report` | `create_report.py` | Spawn a new Report from a SQL query — creates a `kind="table_chat"` session seeded with the rows. Uses `ctx["create_report"]`. |
| `run_in_editor` | `run_in_editor.py` | Database-tab helper-chat only: validate SQL and ask the browser to drop it into the SQL editor and run. The handler itself does no I/O — the browser parses the result and runs the query through the same `/database/query` sandbox. |

Schemas use Anthropic's `tool_use` input_schema format (JSON Schema). The OpenAI adapter translates these at the boundary — see [providers.md](providers.md). Codex strict-mode schemas force optionals into nullable-required; the Codex client strips the resulting `null` args at parse time (`clients/codex.py::_strip_null_values`), so handlers see absent and explicit-null identically.

`TOOLS: list[Tool]` (`registry.py`) is the catalog assembled from the tool modules. Import `TOOLS` when passing to a provider client.

## Data flow for one tool call

```
Turn._execute_one_tool(tool_run)                      backend/domain/agent/turn.py
    │
    ├─ persistence.begin_tool_execution (running + tool_status part)
    │
    ▼
execute_tool_structured(name, input, ctx)             registry.py
    │
    ├─ execute_tool(name, input, ctx)
    │     ├─ tool = _BY_NAME[name]   (unknown name / non-dict input → error envelope)
    │     ├─ await asyncio.to_thread(tool.handler, input, ctx)
    │     ├─ catch SQLValidationError → JSON {"error": ...}
    │     ├─ catch Exception          → JSON {"error": ...}
    │     └─ _append_hint(result_str)
    │           └─ if JSON error matches pattern → append advice to the error string
    │
    └─ parse result → envelope
          { status, tool, content, error, duration_ms }
    │
    ▼
persistence.complete_tool_execution (status + result_part)
```

`Turn` (in `backend/domain/agent/turn.py`, not here) persists the envelope to the store and the runtime yields a `tool_completed` or `tool_failed` event. The **`content` string** (not the parsed dict) is what the model sees on the next turn — so tools must be careful that the JSON they return is legible to the LLM, not just to code.

## Dispatch

`registry.py`. The catalog is keyed by name (`_BY_NAME`); each `Tool.handler` has a uniform signature:

```python
def _handler(input_data: dict, ctx: dict | None) -> str:
    ...  # returns JSON-string result
```

All handlers are synchronous. The dispatcher wraps them in `asyncio.to_thread` so blocking SQLite I/O doesn't stall the event loop. This matters because `Turn.execute_tools` fans out to each tool under `asyncio.gather` — multiple tools from one pass run concurrently, each on its own thread.

Input validation is the handlers' own job — they read fields with `.get()` and return `{"error": ...}` envelopes for anything malformed. (A jsonschema pre-validation layer used to run before dispatch; it was removed because every handler already coped with bad input, and the one real consumer of schema-awareness — Codex null-stripping — moved into the Codex client where the strict schemas are generated.)

## Error hints

`registry.py::_append_hint`. Tool errors are often cryptic SQLite messages (`"no such column: qb_plays"`). A model with no column name often guesses and guesses. To shorten that feedback loop, known error patterns get remediation advice appended to the error string itself:

| Error pattern | Hint |
|---|---|
| `"ambiguous"` | Prefix columns with table name. |
| `"no such column"` / `"not found in table"` / `"not found in any queried table"` | Call `get_schema(table_name)`. |
| `"timed out"` | Add WHERE filters (season, team, or player). |
| `"no join path"` | Join `players` directly via its id columns. |
| `"no such table"` / `"invalid table"` | Lists valid tables. |

Hints are additive: if no pattern matches, the original result is returned unchanged. The full list is `_ERROR_HINTS` in `registry.py`.

This is a prompt-engineering shortcut, not a substitute for the system prompt — the goal is to make one-shot tool errors self-correcting without a dedicated retry loop. Because the advice lives inside the error string, it survives into the transcript, the inspector's Error payload, and the model's next-iteration context with no separate plumbing.

## The SQL sandbox

`sandbox/runner.py`. Four pieces:

### Validation

`validate_sql` (`sandbox/runner.py:86`) — regex whitelist. Only `SELECT` or `WITH` at the start of the statement (tolerating leading whitespace and `--` / `/* */` comments). Anything else raises `SQLValidationError`. Multi-statement inputs are rejected by detecting unquoted semicolons after stripping string literals, quoted identifiers, and comments — DuckDB's `execute()` accepts multiple statements, so the regex needs to refuse them up front rather than rely on the driver.

### Read-only connection

`_run_sql` (`sandbox/runner.py:112`) opens the database with `duckdb.connect(str(DB_PATH), read_only=True)`. DuckDB enforces read-only at the driver level — any whitelist bypass still can't write. Each tool call opens its own connection on its own thread (handlers are sync, dispatched via `asyncio.to_thread`).

### Limits

| Caller | Helper | Row cap | Wall-clock timeout |
|--------|--------|---------|--------------------|
| In-chat queries | `execute_safe_sql` | `MAX_ROWS = 500` | `QUERY_TIMEOUT_SECONDS = 30` |
| CSV exports | `execute_export_sql` | `EXPORT_MAX_ROWS = 10_000` | `EXPORT_TIMEOUT_SECONDS = 60` |
| Report tables | `execute_table_sql(max_rows)` | caller-chosen, clamped to `[1, 500]` | 30 s (same as in-chat) |

DuckDB has no native `set_progress_handler`/op-budget knob, so the timeout is enforced via `threading.Timer + conn.interrupt()` (`runner.py:127`). The timer fires after `timeout_seconds`; `conn.interrupt()` raises `duckdb.InterruptException` from inside the running query, which the runner catches and re-raises as a friendly `SQLValidationError("Query timed out…")`. The timer is cancelled in a `finally` block so a fast query incurs no cleanup cost.

The row cap is enforced two ways: `_ensure_limit` (`runner.py:191`) either appends `LIMIT N` or rewrites a too-high numeric `LIMIT` in-place; and `_clamp_limit_param` clamps any bound `LIMIT ?` parameter at bind time so a parameterized query can't bypass the cap.

### Single-file DuckDB

The sandbox reads exactly one file: `DB_PATH` (set via `.env`) → `nflverse.duckdb`. There is no separate `pbp.db` and no `ATTACH DATABASE` — `play_by_play`, `pbp_participation`, and `ftn_charting` are all tables inside the single DuckDB. Queries reference them by name like any other table.

## The `ctx` side-channel

Handlers have a uniform `(input_data, ctx)` signature, but most ignore `ctx`. It exists so handlers can reach runtime services without importing them. Three tools use it today:

- `create_csv_export` reads `ctx["register_export"]` to register the file in the user's export library.
- `set_table` reads `ctx["persist_table"]` to update the live `TableStateRecord` for the current Report, gated by the lock.
- `create_report` reads `ctx["create_report"]` to spawn a new `kind="table_chat"` session linked back to the originating chat.

`Turn._execute_one_tool` (`backend/domain/agent/turn.py`) builds `ctx` by merging a `register_export` closure with whatever the caller passed via `extra_tool_ctx` — that's how `persist_table` and `create_report` get plumbed in only when running inside a Report context. The Database tab's helper-chat path passes `ctx=None` (no persistence at all); the helper's tool whitelist excludes `set_table` and `create_report` so the missing closures can never be reached. See [database-browser.md](database-browser.md#tool-whitelist).

If `ctx` is `None` (or a key is missing), handlers still return useful output but skip the side-effect — which is what makes them independently testable.

Adding a new side-channel means: (1) build it where the runtime constructs `ctx` (or pass via `extra_tool_ctx`), (2) read it in the handler, (3) handle the missing-key case for tests. No registry to touch.

## Handler contract

Every handler returns a JSON string. The shape is tool-specific but two conventions are enforced:

- **Error envelope.** On expected failure, return `{"error": "message"}`. `execute_tool` also wraps any uncaught `SQLValidationError` or `Exception` in this shape, so handlers don't need defensive `try` blocks around known error sources.
- **Truncation note.** When row caps hit, include a `note` field so the model can warn the user. Helpers in `backend/domain/tools/truncate.py` (`truncate_rows`, `truncate_text`) do this automatically — handlers like `execute_sql.py` just pass their rows through.

`execute_tool_structured` (`registry.py`) parses the result looking for an `error` key; its presence flips `status` to `"error"` in the envelope. Handlers should not set `status` themselves — the dispatcher derives it.

## Adding a new tool

1. Create `backend/domain/tools/<name>.py` with the handler `(input_data, ctx) -> str` and `TOOL = Tool(name=..., description=..., input_schema=..., handler=...)`.
2. Import its `TOOL` in `registry.py` and append it to the `TOOLS` list. That's the only registration point.
3. If the handler needs runtime state, extend `ctx` where it's built in `Turn._execute_one_tool` or via `extra_tool_ctx` (`backend/domain/agent/turn.py`). Otherwise ignore `ctx`.
4. If you want the new tool available to the Database tab's helper chat, also add its name to `ALLOWED_HELPER_TOOLS` in `backend/domain/agent/stateless.py`. Otherwise the stateless loop will reject it with a `ToolFailedEvent` and never dispatch.
5. If tool output can produce novel error strings users should correct, add a `(pattern, hint)` pair to `_ERROR_HINTS` in `registry.py`.

No test fixtures, no registration decorators, no boot-time side effects. The single `Tool` object and the uniform handler signature are the only contracts.
