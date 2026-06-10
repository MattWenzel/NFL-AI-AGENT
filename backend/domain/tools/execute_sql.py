"""Arbitrary read-only SQL tool — the agent's main data access path."""

import logging

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import execute_safe_sql
from backend.domain.tools.truncate import truncate_rows

logger = logging.getLogger(__name__)


def _execute_sql(input_data: dict, ctx: dict | None = None) -> str:
    sql = input_data.get("sql", "")
    result = execute_safe_sql(sql)

    logger.debug("SQL rows=%d truncated=%s", result.row_count, result.truncated)

    extra = {"columns": result.columns, "row_count": result.row_count}
    if result.truncated:
        extra["note"] = f"Results truncated to {result.row_count} rows"

    return truncate_rows(result.rows, total=result.row_count, **extra)


TOOL = Tool(
    name="execute_sql",
    description=(
        "Execute a read-only SQL query against the DuckDB database. "
        "Use this for all data queries: joins, aggregation, window functions, CTEs, UNION, subqueries. "
        "BEFORE querying pfr_advanced, ngs_stats, qbr, combine, or draft_picks for the first time in a conversation, "
        "call get_schema to see the exact column names — these tables use abbreviated or domain-specific naming "
        "that is not reliably memorized in the system prompt, and guessing produces 'no such column' errors. "
        "The query runs in a sandboxed read-only connection with a 30-second timeout and 500-row limit."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL SELECT or WITH statement. Must be read-only (no INSERT/UPDATE/DELETE/DROP).",
            },
        },
        "required": ["sql"],
    },
    handler=_execute_sql,
)
