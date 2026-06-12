"""Tool catalog + execution dispatch.

Each tool lives in its own module as a `Tool` carrying both the schema
and the handler, so a rename can't drift between a definitions table and
a dispatch table — there is only one table, built here from the modules
themselves.

Errors stay actionable: when a tool result is a JSON error matching a
known pattern (missing column, unknown table, timeout, …), a short
remediation hint is appended to the error string so the model can
recover on its next iteration instead of flailing.
"""

import asyncio
import json
import logging
import time

from backend.domain.providers.types import Tool
from backend.domain.tools.create_chart import TOOL as _CREATE_CHART
from backend.domain.tools.create_csv_export import TOOL as _CREATE_CSV_EXPORT
from backend.domain.tools.create_report import TOOL as _CREATE_REPORT
from backend.domain.tools.execute_sql import TOOL as _EXECUTE_SQL
from backend.domain.tools.get_guide import TOOL as _GET_GUIDE
from backend.domain.tools.get_player_info import TOOL as _GET_PLAYER_INFO
from backend.domain.tools.get_schema import TOOL as _GET_SCHEMA
from backend.domain.tools.run_in_editor import TOOL as _RUN_IN_EDITOR
from backend.domain.tools.search_players import TOOL as _SEARCH_PLAYERS
from backend.domain.tools.set_table import TOOL as _SET_TABLE
from backend.domain.tools.sandbox.runner import SQLValidationError

logger = logging.getLogger(__name__)

TOOLS: list[Tool] = [
    _SEARCH_PLAYERS,
    _EXECUTE_SQL,
    _GET_GUIDE,
    _GET_SCHEMA,
    _GET_PLAYER_INFO,
    _CREATE_CSV_EXPORT,
    _SET_TABLE,
    _CREATE_REPORT,
    _CREATE_CHART,
    _RUN_IN_EDITOR,
]

_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


_ERROR_HINTS = [
    ("ambiguous", "Hint: Prefix columns with table name (e.g., season_stats.player_gsis_id)."),
    ("not found in table", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("no such column", "Hint: Call get_schema(table_name) to see available columns before retrying."),
    ("not found in any queried table", "Hint: Call get_schema(table_name) to see available columns."),
    ("timed out", "Hint: Add WHERE filters (season, team, or player). For snap_counts, always filter by season."),
    ("no join path", "Hint: players has player_gsis_id / player_pfr_id / player_espn_id — join directly instead of going through player_ids."),
    ("no such table", "Hint: Valid tables: players, player_ids, games, stadiums, teams, trades, officials, team_game_stats, team_season_stats, game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, draft_picks, combine, injuries, contracts, contracts_cap_breakdown, v_depth_charts (preferred depth-chart view), depth_charts, depth_charts_daily, v_player_careers, v_draft_pick_careers, play_by_play, pbp_participation, ftn_charting."),
    ("invalid table", "Hint: Valid tables: players, player_ids, games, stadiums, teams, trades, officials, team_game_stats, team_season_stats, game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, draft_picks, combine, injuries, contracts, contracts_cap_breakdown, v_depth_charts (preferred depth-chart view), depth_charts, depth_charts_daily, v_player_careers, v_draft_pick_careers, play_by_play, pbp_participation, ftn_charting."),
]


def _classify_error(error_text: str) -> str | None:
    """Return an actionable hint for a known error pattern, or None."""
    lower = error_text.lower()
    for pattern, hint in _ERROR_HINTS:
        if pattern in lower:
            return hint
    return None


def _append_hint(result_str: str) -> str:
    """If result_str is a JSON error matching a known pattern, append the
    remediation advice to the error string itself."""
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
    if hint and hint not in error_text:
        key = "error" if data.get("error") else "detail"
        data[key] = f"{error_text} {hint}"
        return json.dumps(data)
    return result_str


def _summarize_input(input_data: dict) -> str:
    """Short summary of tool input for logging."""
    if "sql" in input_data:
        sql = input_data["sql"]
        return f"sql[{len(sql)} chars]"
    return json.dumps(input_data, default=str)[:200]


async def execute_tool(name: str, input_data: dict, ctx: dict | None = None) -> str:
    """Execute a tool and return the result as a string.

    Tool handlers are synchronous and run via asyncio.to_thread so SQLite
    I/O doesn't block the event loop. Returns JSON (structured data or an
    error envelope), with remediation advice appended to the error string
    when it matches a known pattern.

    `ctx` is an optional side-channel for runtime hooks (e.g. a
    `register_export` callback for `create_csv_export`). Handlers that
    don't need it ignore the argument.
    """
    t0 = time.monotonic()
    try:
        tool = _BY_NAME.get(name)
        if tool is None or tool.handler is None:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        elif not isinstance(input_data, dict):
            result = json.dumps({"error": f"{name} expects an object input"})
        else:
            result = await asyncio.to_thread(tool.handler, input_data, ctx)
    except SQLValidationError as e:
        result = json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in tool %s", name)
        result = json.dumps({"error": str(e)})

    duration = time.monotonic() - t0
    logger.debug("Tool %-18s  %.2fs  input=%s", name, duration, _summarize_input(input_data))
    return _append_hint(result)


async def execute_tool_structured(name: str, input_data: dict, ctx: dict | None = None) -> dict:
    """Execute a tool and return a normalized envelope for persisted tool runs."""
    started = time.monotonic()
    result = await execute_tool(name, input_data, ctx=ctx)
    duration_ms = int((time.monotonic() - started) * 1000)
    error = None
    status = "completed"
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict):
            error = parsed.get("error")
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
        "duration_ms": duration_ms,
    }
