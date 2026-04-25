# Tools

Tools are how the agent does anything non-verbal. Every SQL query, schema lookup, guide load, and CSV export is a tool call from the model. This doc covers the tool registry, the validation → dispatch → handler pipeline, the SQL sandbox, and the `ctx` side-channel.

The runtime's role in tool calls (concurrent dispatch under `asyncio.gather`, result persistence) is covered in [runtime.md](runtime.md#tool-execution-toolexecutionservice).

## File map

The `backend/core/tools/` package is flat — infrastructure modules and handlers sit side by side, one file per tool:

- `backend/core/tools/__init__.py` — public surface: `TOOLS`, `TOOL_DEFINITIONS`, `execute_tool`, `execute_tool_structured`.
- `backend/core/tools/definitions.py` — Anthropic-format tool schemas + typed `TOOLS` list.
- `backend/core/tools/registry.py` — dispatch table, execution helpers (`execute_tool`, `execute_tool_structured`), drift guard.
- `backend/core/tools/validation.py` — JSON-Schema input validation, error-hint injection.
- `backend/core/tools/sandbox.py` — read-only SQL runner with row/op caps and PBP auto-attach.
- `backend/core/tools/truncate.py` — shared `truncate_text` / `truncate_rows` helpers for result formatting.
- `backend/core/tools/schema_metadata.py` — `TABLE_ALIASES` (hand-coded) and `JOIN_EDGES` (auto-derived from DuckDB's `duckdb_constraints()` at import time; one hand-coded supplement for the `v_depth_charts` view which can't carry an FK). Used by `get_schema` for join-graph hints.
- `backend/core/tools/guide_registry.py` — `GUIDE_TOPICS` tuple + `GUIDE_INDEX_ROWS` shown in the system prompt's guide index.
- Handlers, one per tool: `backend/core/tools/execute_sql.py`, `backend/core/tools/player_lookup.py` (both `_search_players` and `_get_player_info`), `backend/core/tools/get_schema.py`, `backend/core/tools/get_guide.py`, `backend/core/tools/create_chart.py`, `backend/core/tools/create_csv_export.py`.
- `backend/core/tools/guides/*.md` — seven markdown guides loaded by `get_guide`: `fantasy.md`, `player_stats.md`, `play_by_play.md`, `drives.md`, `postseason.md`, `player_profile.md`, `games.md`.

## The seven tools

Declared in `backend/core/tools/definitions.py`:

| Tool | Handler | Purpose |
|------|---------|---------|
| `search_players` | `backend/core/tools/player_lookup.py` (`_search_players`) | Fuzzy name/position/team lookup; returns candidates with `gsis_id`. |
| `get_player_info` | `backend/core/tools/player_lookup.py` (`_get_player_info`) | Detailed bio + cross-platform IDs for a given `gsis_id`. |
| `get_guide` | `backend/core/tools/get_guide.py` | Load a topic-specific markdown guide (fantasy, play_by_play, …). |
| `get_schema` | `backend/core/tools/get_schema.py` | Table columns + join edges; loaded on demand to save prompt tokens. |
| `execute_sql` | `backend/core/tools/execute_sql.py` | Arbitrary `SELECT`/`WITH` against nflverse.db (500 rows, ~30s). |
| `create_csv_export` | `backend/core/tools/create_csv_export.py` | Export query results to a downloadable CSV (10k rows, ~60s). |
| `create_chart` | `backend/core/tools/create_chart.py` | Render an inline chart spec (bar/line/scatter/pie) from a query. |

Schemas use Anthropic's `tool_use` input_schema format (JSON Schema). The OpenAI adapter translates these at the boundary — see [providers.md](providers.md).

`TOOLS: list[ToolDefinition]` (`definitions.py:172`) is the typed view derived from the raw `TOOL_DEFINITIONS` dicts. Import `TOOLS` when passing to a provider client; `TOOL_DEFINITIONS` is iterated directly by the validator and the drift guard.

## Data flow for one tool call

```
Turn._execute_one_tool(tool_run)                      backend/core/agent/turn.py
    │
    ├─ persistence.begin_tool_execution (running + tool_status part)
    │
    ▼
execute_tool_structured(name, input, ctx)             registry.py:86
    │
    ├─ validate_tool_input(name, input)               validation.py:89
    │     └─ fail → early-return error envelope (duration_ms=0)
    │
    ├─ execute_tool(name, input, ctx)                 registry.py:56
    │     ├─ fn = _TOOL_DISPATCH[name]
    │     ├─ await asyncio.to_thread(fn, input, ctx)  registry.py:74
    │     ├─ catch SQLValidationError → JSON {"error": ...}
    │     ├─ catch Exception          → JSON {"error": ...}
    │     └─ inject_hint(result_str)                  validation.py:141
    │           └─ if JSON error matches pattern → append "hint" key
    │
    └─ parse result → envelope
          { status, tool, content, error, hint, duration_ms }
    │
    ▼
persistence.complete_tool_execution (status + result_part)
```

`Turn` (in `backend/core/agent/turn.py`, not here) persists the envelope to the store and the runtime yields a `tool_completed` or `tool_failed` event. The **`content` string** (not the parsed dict) is what the model sees on the next turn — so tools must be careful that the JSON they return is legible to the LLM, not just to code.

## The dispatch table

`registry.py:27`. A plain dict from tool name → handler function. Handlers have a uniform signature:

```python
def _handler(input_data: dict, ctx: dict | None) -> str:
    ...  # returns JSON-string result
```

All handlers are synchronous. The dispatcher wraps them in `asyncio.to_thread` (`registry.py:74`) so blocking SQLite I/O doesn't stall the event loop. This matters because `Turn.execute_tools` fans out to each tool under `asyncio.gather` — multiple tools from one pass run concurrently, each on its own thread.

### Registry drift guard

`registry.py:40-45`. Import-time assert that `_TOOL_DISPATCH.keys() == {t["name"] for t in TOOL_DEFINITIONS}`. Without this, a rename in one place (say, adding `create_chart` to definitions but forgetting dispatch) would silently produce "Unknown tool" errors and skip input validation. The assert fails loudly at startup instead.

If you add a tool, you must edit both `definitions.py` and `registry.py`; the guard reminds you.

## Input validation

`validation.py:89`. Validates against the declared `input_schema` using `jsonschema.Draft202012Validator` (validators are cached per tool to avoid re-parsing the schema on every call). Catches:

- **Unknown tool** → `"Unknown tool: <name>"`.
- **Non-object input** → `"<name> expects an object input"`.
- **Any JSON Schema violation** — required fields, enum values, types (string / integer / number / boolean / object / array), `pattern`, array element types, nested objects. Only the first error is reported (sorted by `absolute_path`), so the model gets one clean retry signal instead of a noisy multi-line dump.

Error shape: `"Invalid input for <tool>: <property path>: <message>"`, so the hint injector can point at the offending property.

Before validation, `_strip_codex_nulls` (`validation.py:49`) walks the input alongside the schema and drops `null` values for not-originally-required properties at every nesting level. Codex strict-mode schemas force every property into `required` and make optionals nullable, so Codex sends `{"topic": null, "extra": null}` for unused optionals; handlers use `.get()` + truthiness checks, so absent and explicit-`null` should be identical to them. Stripping lets the validator pass.

Validation runs **before dispatch**. A validation failure short-circuits with an error envelope; the handler is never called. `duration_ms` is forced to 0 so the transcript doesn't falsely show the handler ran.

## Hint injection

`validation.py:141`. Tool errors are often cryptic SQLite messages (`"no such column: qb_plays"`). A model with no column name often guesses and guesses. To shorten that feedback loop, `inject_hint` pattern-matches known error strings and appends a remediation hint to the JSON result:

| Error pattern | Hint |
|---|---|
| `"ambiguous"` | Prefix columns with table name. |
| `"no such column"` / `"not found in table"` / `"not found in any queried table"` | Call `get_schema(table_name)`. |
| `"timed out"` | Add WHERE filters (season, team, or player). |
| `"no join path"` | Use `player_ids` as bridge for snap_counts/pfr_advanced (pfr_id) or qbr (espn_id). |
| `"no such table"` / `"invalid table"` | Lists valid tables. |

Hints are additive: if no pattern matches, the original result is returned unchanged. The full list (`_ERROR_HINTS`) is at `validation.py:120`.

This is a prompt-engineering shortcut, not a substitute for the system prompt — the goal is to make one-shot tool errors self-correcting without a dedicated retry loop.

## The SQL sandbox

`sandbox.py`. Four pieces:

### Validation

`validate_sql` (`sandbox.py:66`) — regex whitelist. Only `SELECT` or `WITH` at the start of the statement (tolerating leading whitespace and `--` / `/* */` comments). Anything else raises `SQLValidationError`. Multi-statement inputs are rejected by SQLite's `sqlite3.Warning` at execute time, not by the regex.

### Read-only connection

`execute_safe_sql` (`sandbox.py:159`) opens the DB with `file:{DB_PATH}?mode=ro`. SQLite enforces read-only at the driver level — any whitelist bypass still can't write. `check_same_thread=False` is safe because each tool call opens its own connection on its own thread.

### Limits

| Knob | `execute_safe_sql` | `execute_export_sql` |
|------|-------------------|----------------------|
| Row cap | `MAX_ROWS = 500` | `EXPORT_MAX_ROWS = 10_000` |
| Op budget | `QUERY_TIMEOUT_OPS = 300M` (~30s) | `EXPORT_TIMEOUT_OPS = 600M` (~60s) |

The op budget is enforced via `conn.set_progress_handler` (`sandbox.py:120`): SQLite calls the handler every N VM instructions; returning non-zero aborts. This is more robust than wall-clock timeout because it runs inside SQLite's vdbe loop.

The row cap is enforced by `_ensure_limit` (`sandbox.py:169`), which either appends `LIMIT N` or rewrites a too-high numeric `LIMIT` in-place. Parameterized `LIMIT ?` is passed through unchanged — the caller is trusted.

### PBP auto-attach

`sandbox.py:19`. Queries matching `\bplay_by_play\b` or `\bpbp\.` trigger `ATTACH DATABASE file:{PBP_DB_PATH}?mode=ro AS pbp` (`sandbox.py:114`). On function exit, the database is detached in a `finally` block. The model references `play_by_play` naturally; it doesn't know `pbp.db` is a separate file.

If `pbp.db` is missing and the query references it, `SQLValidationError` is raised with a clear message (`sandbox.py:110`) — no cryptic SQLite error reaches the model.

## The `ctx` side-channel

Handlers have a uniform `(input_data, ctx)` signature, but most ignore `ctx`. It exists so handlers can reach runtime services without importing them. Right now only `create_csv_export` uses it.

`Turn._execute_one_tool` (`backend/core/agent/turn.py`) builds `ctx`:

```python
ctx = {
    "register_export": lambda meta: self.store.register_export(
        **meta,
        source_session_id=session_id,
        source_tool_run_id=tool_run.id,
    ),
}
```

`create_csv_export` (`backend/core/tools/create_csv_export.py:53`) pulls the callback, calls it after writing the CSV, and unlinks the file if registration fails so orphan files don't accumulate. If `ctx` is `None` (e.g., calling the tool from a test), the handler still returns the download info but skips library registration — this is what makes handlers independently testable.

Adding a new side-channel means: (1) build it in `Turn._execute_one_tool`, (2) read it in the handler, (3) handle the `None` case for tests. No registry to touch.

## Handler contract

Every handler returns a JSON string. The shape is tool-specific but two conventions are enforced:

- **Error envelope.** On expected failure, return `{"error": "message"}`. `execute_tool` also wraps any uncaught `SQLValidationError` or `Exception` in this shape, so handlers don't need defensive `try` blocks around known error sources.
- **Truncation note.** When row caps hit, include a `note` field so the model can warn the user. Helpers in `backend/core/tools/truncate.py` (`truncate_rows`, `truncate_text`) do this automatically — handlers like `execute_sql.py` just pass their rows through.

`execute_tool_structured` (`registry.py:86`) parses the result looking for `error` and `hint` keys; the presence of `error` flips `status` to `"error"` in the envelope. Handlers should not set `status` themselves — the dispatcher derives it.

## Adding a new tool

1. Add the schema dict to `TOOL_DEFINITIONS` in `definitions.py`.
2. Write the handler in `backend/core/tools/<name>.py` with signature `(input_data, ctx) -> str`.
3. Import the handler in `registry.py` and add it to `_TOOL_DISPATCH`. The drift-guard assert will fail otherwise.
4. If the handler needs runtime state, extend `ctx` in `Turn._execute_one_tool` (`backend/core/agent/turn.py`). Otherwise ignore `ctx`.
5. If tool output can produce novel error strings users should correct, add a `(pattern, hint)` pair to `_ERROR_HINTS` in `validation.py`.

No test fixtures, no registration decorators, no boot-time side effects. The drift-guard assert and the uniform handler signature are the only contracts.
