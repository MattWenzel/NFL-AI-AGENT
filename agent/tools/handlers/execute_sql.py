"""Arbitrary read-only SQL tool — the agent's main data access path."""

import logging

from agent.tools.sql_sandbox import execute_safe_sql
from agent.tools._helpers import _truncate_rows

logger = logging.getLogger(__name__)


def _execute_sql(input_data: dict, ctx: dict | None = None) -> str:
    sql = input_data.get("sql", "")
    result = execute_safe_sql(sql)

    logger.debug("SQL rows=%d truncated=%s", result.row_count, result.truncated)

    extra = {"columns": result.columns, "row_count": result.row_count}
    if result.truncated:
        extra["note"] = f"Results truncated to {result.row_count} rows"

    return _truncate_rows(result.rows, total=result.row_count, **extra)
