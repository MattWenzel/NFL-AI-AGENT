"""create_report tool: spin up a new Report (table-view chat) populated
with the agent's SQL result.

Runs the SQL through the same sandbox as `set_table` (capped at the
absolute 500-row ceiling), then asks the runtime to provision a fresh
table-chat session and persist the rows to its `table_states` row. The
runtime emits a `ReportCreatedEvent` on completion; the frontend
renders a clickable link card in the agent's response so the user can
open the new Report from the chat.

The handler returns ONLY a small summary (id + row count + columns) to
the agent — the rows themselves never re-enter the LLM context.
"""

import json
import logging

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import TABLE_MAX_ROWS, execute_table_sql

logger = logging.getLogger(__name__)


def _create_report(input_data: dict, ctx: dict | None = None) -> str:
    sql = input_data.get("sql", "")
    title = input_data.get("title", "")
    if not isinstance(sql, str) or not sql.strip():
        return json.dumps({"error": "Missing required `sql` argument."})
    if not isinstance(title, str) or not title.strip():
        return json.dumps({"error": "Missing required `title` argument."})

    if ctx is None or "create_report" not in ctx:
        return json.dumps({"error": "create_report is unavailable in this context."})

    try:
        result = execute_table_sql(sql, max_rows=TABLE_MAX_ROWS)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if not result.columns:
        return json.dumps({"error": "Query returned no columns."})

    create = ctx["create_report"]
    try:
        report_id = create(
            title=title.strip(),
            columns=result.columns,
            rows=result.rows,
            last_sql=sql,
            row_count=result.row_count,
            truncated=result.truncated,
        )
    except Exception as exc:
        logger.exception("create_report callback failed")
        return json.dumps({"error": f"Could not create report: {exc}"})

    summary = {
        "status": "success",
        "report_id": report_id,
        "title": title.strip(),
        "row_count": result.row_count,
        "columns": result.columns,
        "truncated": result.truncated,
    }
    if result.truncated:
        summary["note"] = (
            f"Result was capped at {TABLE_MAX_ROWS} rows. The full query may "
            "have produced more — narrow filters in a follow-up turn if needed."
        )
    return json.dumps(summary)


TOOL = Tool(
    name="create_report",
    description=(
        "Create a new Report (a table-view chat) populated with the rows from this SQL query, "
        "then auto-navigate the user to it. Use this whenever the user wants to *view, browse, "
        "sort, or iterate on* tabular data — anything beyond a one-shot answer. The user lands "
        "in the Reports tab with the table already filled in and can chat with a fresh agent "
        "there to refine columns, download as CSV, or run follow-ups. "
        "Pick a short descriptive `title` (3-8 words, e.g. 'Top 25 PPR Scorers 2024'). "
        "Rows are capped at 500 server-side. "
        "Prefer this over inline markdown tables when there are >~10 rows or >~5 columns. "
        "Distinct from `create_csv_export`: that tool produces a download link and is only for "
        "explicit 'download/save as CSV' requests."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL SELECT or WITH statement. Must be read-only.",
            },
            "title": {
                "type": "string",
                "description": "Short descriptive title for the Report (3-8 words).",
            },
        },
        "required": ["sql", "title"],
    },
    handler=_create_report,
)
