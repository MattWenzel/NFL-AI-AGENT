"""Tool execution dispatch: validation, invocation, error hints, drift guard."""

import asyncio
import json
import logging
import time

from agent.tools.sql_sandbox import SQLValidationError
from agent.tools.definitions import TOOL_DEFINITIONS
from agent.tools.create_csv_export import _create_csv_export
from agent.tools.execute_sql import _execute_sql
from agent.tools.get_schema import _get_schema
from agent.tools.player_lookup import _get_player_info, _search_players

logger = logging.getLogger(__name__)


# ---------- Dispatch ----------

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


# ---------- Input validation ----------

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


def _summarize_input(input_data: dict) -> str:
    """Create a short summary of tool input for logging."""
    if "sql" in input_data:
        sql = input_data["sql"]
        return f"sql[{len(sql)} chars]"
    return json.dumps(input_data, default=str)[:200]


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


# ---------- Execution ----------

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
