"""Tool execution dispatch: route a tool call to its handler.

Input validation and error hint injection live in `validation.py`. This
module owns only the dispatch table, execution helpers, and the
registry-drift guard that catches renames across definitions and
handlers.
"""

import asyncio
import json
import logging
import time

from backend.domain.tools.definitions import TOOL_DEFINITIONS
from backend.domain.tools.handlers.create_chart import _create_chart
from backend.domain.tools.handlers.create_csv_export import _create_csv_export
from backend.domain.tools.handlers.create_report import _create_report
from backend.domain.tools.handlers.execute_sql import _execute_sql
from backend.domain.tools.handlers.get_guide import _load_guide
from backend.domain.tools.handlers.get_schema import _get_schema
from backend.domain.tools.handlers.player_lookup import _get_player_info, _search_players
from backend.domain.tools.handlers.run_in_editor import _run_in_editor
from backend.domain.tools.handlers.set_table import _set_table
from backend.domain.tools.sandbox.runner import SQLValidationError
from backend.domain.tools.validation import inject_hint, validate_tool_input

logger = logging.getLogger(__name__)


_TOOL_DISPATCH = {
    "search_players": _search_players,
    "execute_sql": _execute_sql,
    "get_guide": _load_guide,
    "get_schema": _get_schema,
    "get_player_info": _get_player_info,
    "create_csv_export": _create_csv_export,
    "create_chart": _create_chart,
    "create_report": _create_report,
    "set_table": _set_table,
    "run_in_editor": _run_in_editor,
}

# Registry drift guard: every dispatch entry must map to a TOOL_DEFINITIONS
# entry and vice versa. Otherwise a rename in one place produces silent
# "Unknown tool" errors or skips input validation. Import-time assert.
_dispatch_names = set(_TOOL_DISPATCH)
_definition_names = {t["name"] for t in TOOL_DEFINITIONS}
assert _dispatch_names == _definition_names, (
    f"Tool registry drift: dispatch={_dispatch_names} "
    f"vs definitions={_definition_names}"
)


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
    error envelope), with a remediation hint appended when the error
    matches a known pattern.

    `ctx` is an optional side-channel for runtime hooks (e.g. a
    `register_export` callback for `create_csv_export`). Handlers that
    don't need it ignore the argument.
    """
    t0 = time.monotonic()
    try:
        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        else:
            result = await asyncio.to_thread(fn, input_data, ctx)
    except SQLValidationError as e:
        result = json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected error in tool %s", name)
        result = json.dumps({"error": str(e)})

    duration = time.monotonic() - t0
    logger.debug("Tool %-18s  %.2fs  input=%s", name, duration, _summarize_input(input_data))
    return inject_hint(result)


async def execute_tool_structured(name: str, input_data: dict, ctx: dict | None = None) -> dict:
    """Execute a tool and return a normalized envelope for persisted tool runs."""
    validation_error = validate_tool_input(name, input_data)
    if validation_error:
        return {
            "status": "error",
            "tool": name,
            "content": json.dumps({"status": "error", "error": validation_error}),
            "error": validation_error,
            "hint": None,
            "duration_ms": 0,
        }

    started = time.monotonic()
    result = await execute_tool(name, input_data, ctx=ctx)
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
