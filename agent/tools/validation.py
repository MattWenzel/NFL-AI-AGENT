"""Tool input validation and error hint injection.

Keeps tool errors actionable:
- `validate_tool_input` rejects malformed input before dispatch, citing the
  schema key that failed.
- `inject_hint` post-processes tool JSON errors to append a short
  remediation hint for known patterns (ambiguous column, missing table,
  timeout, etc.).
"""

import json

from agent.tools.definitions import TOOL_DEFINITIONS


def _get_tool_definition(name: str) -> dict | None:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None


def validate_tool_input(name: str, input_data: dict) -> str | None:
    """Return an error string if input_data violates the tool's schema, else None."""
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


def inject_hint(result_str: str) -> str:
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
