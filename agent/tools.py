"""Tool definitions and execution dispatch for the NFL stats agent."""

import asyncio
import csv
import io
import json
import logging
import re
import time
from pathlib import Path

from agent.providers.base import ToolDefinition
from agent.sql_sandbox import execute_safe_sql, execute_export_sql, SQLValidationError
from config import EXPORTS_DIR, format_file_size

logger = logging.getLogger(__name__)

TOOL_RESULT_MAX_CHARS = 8000

# ---------- Tool definitions (Anthropic tool_use format) ----------

TOOL_DEFINITIONS = [
    {
        "name": "search_players",
        "description": "Search for NFL players by name, position, or team. Use this FIRST whenever a user mentions a player name to resolve their gsis_id for subsequent queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Player name to search (partial match supported, e.g. 'Mahomes', 'Patrick Mahomes')",
                },
                "position": {
                    "type": "string",
                    "description": "Filter by position (QB, RB, WR, TE, etc.)",
                },
                "team": {
                    "type": "string",
                    "description": "Filter by current team abbreviation (KC, BUF, etc.)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10)",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query against the SQLite database. "
            "Use this for all data queries: joins, aggregation, window functions, CTEs, UNION, subqueries. "
            "The query runs in a sandboxed read-only connection with a 10-second timeout and 500-row limit. "
            "For play-by-play data, reference the table as play_by_play (it auto-attaches pbp.db)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement. Must be read-only (no INSERT/UPDATE/DELETE/DROP).",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "get_schema",
        "description": "Get the schema (columns, types, join edges) for a specific table. Use this when you need exact column names or want to explore what data is available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name: players, player_ids, game_stats, season_stats, games, draft_picks, combine, snap_counts, ngs_stats, depth_charts, depth_charts_2025, pfr_advanced, qbr, play_by_play",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_player_info",
        "description": "Get detailed player biography and cross-platform IDs for a specific player. Requires gsis_id (use search_players first to find it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "gsis_id": {
                    "type": "string",
                    "description": "Player's GSIS ID (e.g. '00-0033873' for Patrick Mahomes)",
                },
            },
            "required": ["gsis_id"],
        },
    },
    {
        "name": "create_csv_export",
        "description": (
            "Export SQL query results to a downloadable CSV file. "
            "Use this AFTER previewing data with execute_sql and confirming with the user. "
            "Supports up to 10,000 rows with a 30-second timeout. "
            "Returns a download link for the CSV file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement to export. Must be read-only.",
                },
                "filename": {
                    "type": "string",
                    "description": "Descriptive filename without extension (e.g. 'qb_passing_stats_2024', 'top_receivers_ppr'). Will be sanitized.",
                },
            },
            "required": ["sql", "filename"],
        },
    },
]

# Typed tool definitions — preferred import for consumers
TOOLS: list[ToolDefinition] = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]


# ---------- Tool execution ----------


def _get_tool_definition(name: str) -> dict | None:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None


def _validate_tool_input(name: str, input_data: dict) -> str | None:
    tool = _get_tool_definition(name)
    if tool is None:
        return f"Unknown tool: {name}"
    schema = tool.get("input_schema", {})
    if schema.get("type") != "object":
        return None
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(input_data, dict):
        return f"{name} expects an object input"
    for key in required:
        if key not in input_data:
            return f"Missing required field '{key}' for {name}"
    type_map = {"string": str, "integer": int, "object": dict}
    for key, value in input_data.items():
        if key not in properties:
            continue
        expected = properties[key].get("type")
        py_type = type_map.get(expected)
        if py_type and not isinstance(value, py_type):
            return f"Field '{key}' for {name} must be {expected}"
    return None


async def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool and return the result as a string.

    All tool functions are synchronous and run via asyncio.to_thread
    so SQLite I/O doesn't block the event loop.
    Returns a JSON string for structured data or an error message.
    """
    t0 = time.monotonic()
    try:
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        else:
            result = await asyncio.to_thread(fn, input_data)
    except SQLValidationError as e:
        result = json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in tool %s", name)
        result = json.dumps({"error": str(e)})

    duration = time.monotonic() - t0
    logger.debug("Tool %-18s  %.2fs  input=%s", name, duration, _summarize_input(input_data))
    return _inject_hint(result)


async def execute_tool_structured(name: str, input_data: dict) -> dict:
    """Execute a tool and return a normalized envelope for persisted tool runs."""
    validation_error = _validate_tool_input(name, input_data)
    if validation_error:
        envelope = {
            "status": "error",
            "tool": name,
            "content": json.dumps({"status": "error", "error": validation_error}),
            "error": validation_error,
            "hint": None,
            "duration_ms": 0,
        }
        return envelope

    started = time.monotonic()
    result = await execute_tool(name, input_data)
    duration_ms = int((time.monotonic() - started) * 1000)
    error = None
    hint = None
    status = "completed"
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            hint = parsed.get("hint")
            if error:
                status = "error"
    except (json.JSONDecodeError, TypeError):
        parsed = None

    content = result if isinstance(result, str) else json.dumps(result)
    return {
        "status": status,
        "tool": name,
        "content": content,
        "error": error,
        "hint": hint,
        "duration_ms": duration_ms,
    }


def _summarize_input(input_data: dict) -> str:
    """Create a short summary of tool input for logging."""
    if "sql" in input_data:
        sql = input_data["sql"]
        return f"sql[{len(sql)} chars]"
    return json.dumps(input_data, default=str)[:200]


def _truncate(text: str) -> str:
    """Truncate plain-text tool result to max chars (fallback for non-structured data)."""
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return text[:TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"


def _truncate_rows(rows: list[dict], key: str = "rows", **extra) -> str:
    """Serialize rows to JSON, dropping trailing rows if over the char limit.

    Produces valid JSON even when truncation is needed, unlike character-level
    truncation which can break mid-object.  Extra keyword arguments (e.g.
    columns, row_count) are included in the output dict.
    """
    total = extra.pop("total", len(rows))
    output = {**extra, key: rows, "total": total}
    serialized = json.dumps(output)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return serialized

    # Binary search for the max number of rows that fit
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = {**extra, key: rows[:mid], "total": total, "note": f"Showing {mid} of {total} rows (truncated to fit)"}
        if len(json.dumps(candidate)) <= TOOL_RESULT_MAX_CHARS:
            lo = mid
        else:
            hi = mid - 1

    if lo == 0:
        # Single row too large — character-truncate the first row so the LLM
        # gets enough context to refine its query (e.g. select fewer columns).
        first = json.dumps(rows[0])[:TOOL_RESULT_MAX_CHARS - 200]
        return json.dumps({**extra, "total": total, "note": "First row too large to fit — showing truncated preview", "preview": first})

    truncated = {**extra, key: rows[:lo], "total": total, "note": f"Showing {lo} of {total} rows (truncated to fit)"}
    return json.dumps(truncated)


# ---------- Error hint injection ----------

_ERROR_HINTS = [
    ("ambiguous", "Hint: Prefix columns with table name (e.g., season_stats.gsis_id)."),
    ("not found in table", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("no such column", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("not found in any queried table", "Hint: Call get_schema(table_name) to see available columns."),
    ("timed out", "Hint: Add WHERE filters (season, team, or player). For snap_counts, always filter by season."),
    ("no join path", "Hint: Use player_ids as bridge for snap_counts/pfr_advanced (pfr_id) or qbr (espn_id)."),
    ("no such table", "Hint: Valid tables: players, player_ids, game_stats, season_stats, games, draft_picks, combine, snap_counts, ngs_stats, depth_charts, depth_charts_2025, pfr_advanced, qbr, play_by_play."),
    ("invalid table", "Hint: Valid tables: players, player_ids, game_stats, season_stats, games, draft_picks, combine, snap_counts, ngs_stats, depth_charts, depth_charts_2025, pfr_advanced, qbr, play_by_play."),
]


def _classify_error(error_text: str) -> str | None:
    """Return an actionable hint for a known error pattern, or None."""
    lower = error_text.lower()
    for pattern, hint in _ERROR_HINTS:
        if pattern in lower:
            return hint
    return None


def _inject_hint(result_str: str) -> str:
    """If result_str is a JSON error, append a hint key when a known pattern matches."""
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str
    if not isinstance(data, dict):
        return result_str
    error_text = data.get("error") or data.get("detail")
    if not error_text or not isinstance(error_text, str):
        return result_str
    hint = _classify_error(error_text)
    if hint:
        data["hint"] = hint
        return json.dumps(data)
    return result_str


def _search_players(input_data: dict) -> str:
    conditions = []
    params = []
    if input_data.get("name"):
        conditions.append("display_name LIKE ?")
        params.append(f"%{input_data['name']}%")
    if input_data.get("position"):
        conditions.append("position = ?")
        params.append(input_data["position"])
    if input_data.get("team"):
        conditions.append("latest_team = ?")
        params.append(input_data["team"])
    try:
        raw_limit = int(input_data.get("limit", 10))
    except (ValueError, TypeError):
        raw_limit = 10
    limit = max(1, min(raw_limit, 50))

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT gsis_id, display_name, position, latest_team FROM players{where} LIMIT ?"

    result = execute_safe_sql(sql, tuple(params) + (limit,))
    return _truncate_rows(result.rows, "data")


def _execute_sql(input_data: dict) -> str:
    sql = input_data.get("sql", "")
    result = execute_safe_sql(sql)

    logger.debug("SQL rows=%d truncated=%s", result.row_count, result.truncated)

    extra = {"columns": result.columns, "row_count": result.row_count}
    if result.truncated:
        extra["note"] = f"Results truncated to {result.row_count} rows"

    return _truncate_rows(result.rows, total=result.row_count, **extra)


def _get_schema(input_data: dict) -> str:
    from api.schema_registry import build_schema_response, build_table_schema
    table_name = input_data.get("table_name", "")
    if table_name:
        result = build_table_schema(table_name)
        if result is None:
            return json.dumps({"error": f"Unknown table: {table_name}"})
    else:
        result = build_schema_response()
    return _truncate(json.dumps(result))


def _get_player_info(input_data: dict) -> str:
    gsis_id = input_data.get("gsis_id", "")
    result = {}

    # Player bio
    try:
        bio = execute_safe_sql(
            "SELECT * FROM players WHERE gsis_id = ? LIMIT 1", (gsis_id,)
        )
        if bio.rows:
            result["player"] = bio.rows[0]
        else:
            return json.dumps({"error": f"No player found with gsis_id '{gsis_id}'"})
    except Exception as e:
        return json.dumps({"error": f"Player lookup failed: {e}"})

    # Cross-platform IDs (soft — player was found, IDs are supplementary)
    try:
        ids = execute_safe_sql(
            "SELECT * FROM player_ids WHERE gsis_id = ? LIMIT 1", (gsis_id,)
        )
        if ids.rows:
            result["ids"] = ids.rows[0]
        else:
            result["ids_note"] = "No cross-platform IDs found"
    except Exception as e:
        result["ids_note"] = f"Could not fetch cross-platform IDs: {e}"

    return _truncate(json.dumps(result))


# ---------- CSV Export ----------


def _sanitize_filename(name: str) -> str:
    """Sanitize filename: keep alphanumeric, hyphens, underscores only."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:80] or "export"


def _create_csv_export(input_data: dict) -> str:
    sql = input_data.get("sql", "")
    raw_filename = input_data.get("filename", "export")

    result = execute_export_sql(sql)

    if not result.rows:
        return json.dumps({"error": "Query returned no rows. Adjust filters and try again."})

    # Build filename with timestamp
    sanitized = _sanitize_filename(raw_filename)
    timestamp = int(time.time())
    csv_filename = f"{sanitized}_{timestamp}.csv"

    # Ensure exports directory exists
    EXPORTS_DIR.mkdir(exist_ok=True)
    csv_path = EXPORTS_DIR / csv_filename

    # Write CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=result.columns)
    writer.writeheader()
    writer.writerows(result.rows)
    csv_path.write_text(buf.getvalue(), encoding="utf-8")

    size_display = format_file_size(csv_path.stat().st_size)

    output = {
        "download_url": f"/exports/{csv_filename}",
        "filename": csv_filename,
        "row_count": result.row_count,
        "columns": result.columns,
        "file_size": size_display,
    }
    if result.truncated:
        output["note"] = f"Results truncated to {result.row_count} rows (export limit)"
    return json.dumps(output)


# Dispatch table — defined after all tool functions
_TOOL_DISPATCH = {
    "search_players": _search_players,
    "execute_sql": _execute_sql,
    "get_schema": _get_schema,
    "get_player_info": _get_player_info,
    "create_csv_export": _create_csv_export,
}

# Guard against registry drift: every dispatch entry must have a matching
# TOOL_DEFINITIONS entry (and vice versa). A rename in one but not the other
# would otherwise produce silent "Unknown tool" errors or skip input
# validation entirely. Import-time assert — fails loudly on first import.
_dispatch_names = set(_TOOL_DISPATCH)
_definition_names = {t["name"] for t in TOOL_DEFINITIONS}
assert _dispatch_names == _definition_names, (
    f"Tool registry drift: dispatch={_dispatch_names} "
    f"vs definitions={_definition_names}"
)
