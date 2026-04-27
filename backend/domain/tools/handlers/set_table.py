"""set_table tool: replace the live table in a table-view chat.

Runs the SQL query through the same sandbox as `execute_sql`, but caps
rows at the user-chosen `table_max_rows` (passed via tool ctx, NOT
provided by the agent). The result rows are persisted via the
`persist_table` ctx callback — they go to the UI as a stream event,
not back into the LLM's context. The agent only learns "table now has
N rows × M columns" so big tables don't bloat the prompt.
"""

import json
import logging

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

    # When the user picks "Auto" the request omits `table_max_rows`; default
    # to the sandbox's absolute ceiling so the agent's own LIMIT is honored.
    max_rows = int(ctx.get("table_max_rows") or TABLE_MAX_ROWS)

    try:
        result = execute_table_sql(sql, max_rows=max_rows)
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
        "max_rows": max_rows,
        "truncated": result.truncated,
    }
    if result.truncated:
        summary["note"] = (
            f"Result was capped at {max_rows} rows (the user's chosen table size). "
            "The full query may have produced more — narrow filters or change the size dropdown if needed."
        )
    return json.dumps(summary)
