"""Chart generation tool: run SQL and return a chart envelope the UI renders inline.

The returned JSON is not a prose preview — the browser thread detects
any completed `create_chart` tool run and replaces the usual JSON code
block with a Chart.js canvas driven by the embedded spec and rows.

Row cap: 500. Charts become unreadable beyond that, and the transcript
would bloat if every chart embedded 5000 data points. The agent should
aggregate upstream (GROUP BY / LIMIT) before calling this.
"""

import json
import logging

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import execute_safe_sql

logger = logging.getLogger(__name__)

_VALID_TYPES = {"bar", "line", "scatter", "pie"}
_ROW_CAP = 500


def _create_chart(input_data: dict, ctx: dict | None = None) -> str:
    sql = input_data.get("sql", "")
    chart_type = input_data.get("chart_type", "")
    x = input_data.get("x", "")
    y = input_data.get("y", "")
    group_by = input_data.get("group_by") or None
    title = input_data.get("title") or None

    if chart_type not in _VALID_TYPES:
        return json.dumps({
            "error": f"Invalid chart_type '{chart_type}'. Choose one of: bar, line, scatter, pie.",
        })

    result = execute_safe_sql(sql)
    if not result.rows:
        return json.dumps({"error": "Query returned no rows. Adjust filters and try again."})

    columns = set(result.columns)
    missing = [c for c in (x, y, group_by) if c and c not in columns]
    if missing:
        return json.dumps({
            "error": (
                f"Column(s) {missing} not in SELECT output. "
                f"Available columns: {result.columns}. "
                f"Alias expressions in your SELECT so the names match."
            ),
        })

    rows = result.rows[:_ROW_CAP]
    truncated = len(result.rows) > _ROW_CAP

    spec = {
        "type": chart_type,
        "x": x,
        "y": y,
        "group_by": group_by,
        "title": title,
    }
    envelope = {
        "chart_spec": spec,
        "columns": result.columns,
        "row_count": len(rows),
        "total_rows": result.row_count,
        "data": rows,
    }
    if truncated:
        envelope["note"] = f"Rendered first {_ROW_CAP} of {result.row_count} rows — aggregate further if more detail is needed."
    return json.dumps(envelope)


TOOL = Tool(
    name="create_chart",
    description=(
        "Generate a chart from a SQL query. Use when a visual comparison answers the question "
        "better than prose — side-by-side player stats, yearly trends, category breakdowns. "
        "The chart renders inline in your response. Prefer execute_sql for tabular answers; "
        "use create_chart when the user would benefit from seeing the shape of the data. "
        "Keep rows aggregated (GROUP BY, LIMIT 10-50) so the chart stays readable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL SELECT or WITH returning the rows to plot. Same rules as execute_sql (read-only).",
            },
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "scatter", "pie"],
                "description": "bar=category comparison, line=trend, scatter=correlation, pie=composition",
            },
            "x": {
                "type": "string",
                "description": "Column name for the X axis / category labels (must exist in the SELECT)",
            },
            "y": {
                "type": "string",
                "description": "Column name for the Y axis / values (must exist in the SELECT, should be numeric)",
            },
            "group_by": {
                "type": "string",
                "description": "Optional: column to split into multiple series (bar/line only)",
            },
            "title": {
                "type": "string",
                "description": "Short chart title shown above the rendered chart",
            },
        },
        "required": ["sql", "chart_type", "x", "y"],
    },
    handler=_create_chart,
)
