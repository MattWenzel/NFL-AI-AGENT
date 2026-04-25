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

from core.tools.sandbox import execute_safe_sql

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
