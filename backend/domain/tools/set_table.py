"""set_table tool: replace the live table in a table-view chat.

Runs the SQL query through the same sandbox as `execute_sql`, capped at
the sandbox's absolute row ceiling. Results are persisted via the
`persist_table` ctx callback — the rows go to the UI as a stream event,
not back into the LLM's context. The agent only learns "table now has
N rows × M columns".

If the user has locked the table, the tool short-circuits with a
structured error envelope before running any SQL — the agent should
ask the user to unlock first.
"""

import json
import logging

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import TABLE_MAX_ROWS, execute_table_sql

logger = logging.getLogger(__name__)


def _set_table(input_data: dict, ctx: dict | None = None) -> str:
    sql = input_data.get("sql", "")
    if not isinstance(sql, str) or not sql.strip():
        return json.dumps({"error": "Missing required `sql` argument."})

    if ctx is None or "persist_table" not in ctx:
        # The tool can only run inside the table-chat runtime path.
        # Without a persist hook there's nowhere to put the rows.
        return json.dumps({"error": "set_table is only available in table-view chats."})

    is_locked = ctx.get("is_table_locked")
    if callable(is_locked) and is_locked():
        return json.dumps({
            "error": (
                "Table is locked. Tell the user the table is locked and ask "
                "them to unlock it via the lock toggle on the table toolbar "
                "before you can change it."
            ),
            "locked": True,
        })

    try:
        result = execute_table_sql(sql, max_rows=TABLE_MAX_ROWS)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if not result.columns:
        return json.dumps({"error": "Query returned no columns."})

    persist = ctx["persist_table"]
    try:
        persist(
            columns=result.columns,
            rows=result.rows,
            sql=sql,
            row_count=result.row_count,
            truncated=result.truncated,
        )
    except Exception as exc:
        logger.exception("persist_table callback failed")
        return json.dumps({"error": f"Could not save table: {exc}"})

    summary = {
        "status": "success",
        "row_count": result.row_count,
        "columns": result.columns,
        "truncated": result.truncated,
    }
    if result.truncated:
        summary["note"] = (
            f"Result was capped at {TABLE_MAX_ROWS} rows. "
            "Narrow filters in a follow-up turn if the user wanted more."
        )
    return json.dumps(summary)


TOOL = Tool(
    name="set_table",
    description=(
        "Replace the live table in a Table View chat with the rows produced by this SQL query. "
        "Only available in 'Change table' turns of a table-view chat — never in regular chat. "
        "The user has selected a row cap via the table-size dropdown; the cap is enforced server-side, "
        "so write LIMIT clauses up to that cap and don't try to exceed it. The rows do NOT come back "
        "in the tool result — only a brief summary (row_count, columns) — so build the SQL to be "
        "self-contained and don't expect to inspect the cells. Standard sandbox rules: SELECT/WITH only, "
        "single statement, read-only. Call this exactly once per 'Change table' turn."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL SELECT or WITH statement. Read-only. Result becomes the new table.",
            },
        },
        "required": ["sql"],
    },
    handler=_set_table,
)
