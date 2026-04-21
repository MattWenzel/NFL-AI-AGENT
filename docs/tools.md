# Tools

Tools are how the agent does anything non-verbal. Every SQL query, schema lookup, guide load, and CSV export is a tool call from the model. This doc covers the tool registry, the validation → dispatch → handler pipeline, the SQL sandbox, and the `ctx` side-channel.

The runtime's role in tool calls (concurrent dispatch under `asyncio.gather`, result persistence) is covered in [runtime.md](runtime.md#tool-execution-_execute_tool).

## File map

- `tools/__init__.py` — public surface: `TOOLS`, `TOOL_DEFINITIONS`, `execute_tool`, `execute_tool_structured`.
- `tools/definitions.py` — Anthropic-format tool schemas + typed `TOOLS` list.
- `tools/registry.py` — dispatch table, execution helpers, drift guard.
- `tools/validation.py` — input validation, error-hint injection.
- `tools/sandbox.py` — read-only SQL runner with row/timeout caps.
- `tools/` — one file per tool.

## The seven tools

Declared in `definitions.py:5`:

| Tool | Handler | Purpose |
|------|---------|---------|
| `search_players` | `handlers/player_lookup.py` | Fuzzy name/position/team lookup; returns candidates with `gsis_id`. |
| `get_player_info` | `handlers/player_lookup.py` | Detailed bio + cross-platform IDs for a given `gsis_id`. |
| `get_guide` | `handlers/get_guide.py` | Load a topic-specific markdown guide (fantasy, play_by_play, etc.). |
| `get_schema` | `handlers/get_schema.py` | Table columns + join edges; loaded on demand to save prompt tokens. |
| `execute_sql` | `handlers/execute_sql.py` | Arbitrary `SELECT`/`WITH` against nflverse.db (500 rows, ~30s). |
| `create_csv_export` | `handlers/create_csv_export.py` | Export query results to a downloadable CSV (10k rows, ~60s). |
| `create_chart` | `handlers/create_chart.py` | Render an inline chart spec (bar/line/scatter/pie) from a query. |

Schemas use Anthropic's `tool_use` input_schema format (JSON Schema subset). The OpenAI adapter translates these at the boundary — see [providers.md](providers.md).

`TOOLS: list[ToolDefinition]` (`definitions.py:192`) is the typed view. Import this in new code; `TOOL_DEFINITIONS` (raw dicts) is kept only because validation and hint classification iterate the raw form.

## Data flow for one tool call

```
ChatRuntime._execute_tool(tool_run)
    │
    ▼
execute_tool_structured(name, input, ctx)          # registry.py:86
    │
    ├─ validate_tool_input(name, input)            # validation.py:23
    │     └─ fail → return error envelope
    │
    ├─ execute_tool(name, input, ctx)              # registry.py:56
    │     ├─ fn = _TOOL_DISPATCH[name]
    │     ├─ await asyncio.to_thread(fn, input, ctx)   # blocking SQLite safe
    │     ├─ catch SQLValidationError  → JSON {"error": ...}
    │     └─ catch Exception           → JSON {"error": ...}
    │
    ├─ inject_hint(result_str)                     # validation.py:70
    │     └─ if JSON error matches pattern → append "hint" key
    │
    └─ parse result → envelope
          { status, tool, content, error, hint, duration_ms }
```

The runtime persists the envelope to the store and yields a `tool_completed` or `tool_failed` event. The **content** string (not the parsed dict) is what the model sees on the next turn — so tools must be careful that the JSON they return is legible to the LLM, not just to code.

## The dispatch table

`registry.py:27`. A plain dict from tool name → handler function. Handlers have a uniform signature:

```python
def _handler(input_data: dict, ctx: dict | None) -> str:
    ...  # returns JSON-string result
```

All handlers are synchronous. The dispatcher wraps them in `asyncio.to_thread` (`registry.py:74`) so blocking SQLite I/O doesn't stall the event loop. This matters because `_execute_tool` is called under `asyncio.gather` — multiple tools from one pass run concurrently, each on its own thread.

### Registry drift guard

`registry.py:42`. Import-time assert that `_TOOL_DISPATCH.keys() == {t["name"] for t in TOOL_DEFINITIONS}`. Without this, a rename in one place (say, adding `create_chart` to definitions but forgetting dispatch) would silently produce "Unknown tool" errors and skip input validation. The assert fails loudly at startup instead.

If you add a tool, you must edit both `definitions.py` and `registry.py`; the guard reminds you.

## Input validation

`validation.py:23`. Three checks against the declared `input_schema`:

1. **Known tool.** `validate_tool_input("unknown", ...)` returns `"Unknown tool: unknown"`.
2. **Required fields present.** Missing any required key → `"Missing required field 'sql' for execute_sql"`.
3. **Primitive type match.** `string`/`integer`/`object` are enforced. Other JSON-Schema features (`enum`, `pattern`, `minimum`, etc.) are **not** checked here — they rely on the provider side or the handler itself.

Validation runs **before dispatch**. A validation failure short-circuits with an error envelope; the handler is never called. `duration_ms` is forced to 0 in that case so the transcript doesn't falsely show the handler ran.

## Hint injection

`validation.py:70`. Tool errors are often cryptic SQLite messages (`"no such column: qb_plays"`). A model with no column name often guesses and guesses. To shorten that feedback loop, `inject_hint` pattern-matches known error strings and appends a remediation hint to the JSON result:

| Error pattern | Hint |
|---|---|
| `"ambiguous"` | Prefix columns with table name. |
| `"no such column"` / `"not found in table"` | Call `get_schema(table_name)`. |
| `"timed out"` | Add WHERE filters. |
| `"no join path"` | Use `player_ids` as bridge. |
| `"no such table"` / `"invalid table"` | (Lists valid tables.) |

Hints are additive: if no pattern matches, the original result is returned unchanged. The full list is at `validation.py:49`.

This is a prompt-engineering shortcut, not a substitute for the system prompt — the goal is to make one-shot tool errors self-correcting without a dedicated retry loop.

## The SQL sandbox

`sandbox.py`. Four pieces:

### Validation

`validate_sql` (`sandbox.py:66`) — regex whitelist. Only `SELECT` or `WITH` at the start of the statement (tolerating leading whitespace and `--` / `/* */` comments). Anything else raises `SQLValidationError`. Multi-statement inputs are rejected by SQLite's `sqlite3.Warning` at execute time, not by the regex.

### Read-only connection

Opened with `file:{DB_PATH}?mode=ro` (`sandbox.py:100`). SQLite enforces read-only at driver level — even malformed whitelist bypasses cannot write. `check_same_thread=False` is safe because each tool call opens its own connection on its own thread.

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

`ChatRuntime._execute_tool` (`runtime.py:358`) builds `ctx`:

```python
ctx = {
    "register_export": lambda meta: self.store.register_export(
        **meta,
        source_session_id=session_id,
        source_tool_run_id=tool_run.id,
    ),
}
```

`create_csv_export` (`handlers/create_csv_export.py:53`) pulls the callback, calls it after writing the CSV, and unlinks the file if registration fails so orphan files don't accumulate. If `ctx` is `None` (e.g., calling the tool from a test), the handler still returns the download info but skips library registration — this is what makes handlers independently testable.

Adding a new side-channel means: (1) build it in `_execute_tool`, (2) read it in the handler, (3) handle the `None` case for tests. No registry to touch.

## Handler contract

Every handler returns a JSON string. The shape is tool-specific but two conventions are enforced:

- **Error envelope.** On expected failure, return `{"error": "message"}`. `execute_tool` also wraps any uncaught `SQLValidationError` or `Exception` in this shape, so handlers don't need defensive `try` blocks around known error sources.
- **Truncation note.** When row caps hit, include a `note` field so the model can warn the user (see `execute_sql.py:19`).

`execute_tool_structured` (`registry.py:86`) parses the result looking for `error` and `hint` keys; the presence of `error` flips `status` to `"error"` in the envelope. Handlers should not set `status` themselves — the dispatcher derives it.

## Adding a new tool

1. Add the schema dict to `TOOL_DEFINITIONS` in `definitions.py`.
2. Write the handler in `tools/<name>.py` with signature `(input_data, ctx) -> str`.
3. Import the handler in `registry.py` and add it to `_TOOL_DISPATCH`. The drift-guard assert will fail otherwise.
4. If the handler needs runtime state, extend `ctx` in `runtime.py:358`. Otherwise ignore `ctx`.
5. If tool output can produce novel error strings users should correct, add a `(pattern, hint)` pair to `_ERROR_HINTS` in `validation.py`.

No test fixtures, no registration decorators, no boot-time side effects. The drift-guard assert and the uniform handler signature are the only contracts.
