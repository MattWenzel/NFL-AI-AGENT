"""`run_in_editor` tool handler.

A "remote-control" affordance for the Database browser's helper chat:
the agent calls this with a SQL string, the handler validates it, and
the SSE layer carries the SQL out to the browser as part of the tool
result. The frontend sees `name="run_in_editor"`, parses the SQL out of
the `content`, and pushes it into the SQL editor.

Nothing executes server-side here — the user-side editor will issue its
own `/database/query` call against the same sandbox once it receives the
SQL. Validating up front means the agent gets immediate feedback if the
query is malformed, instead of waiting for the user-side run to fail.
"""

from __future__ import annotations

import json

from backend.domain.tools.sandbox.runner import SQLValidationError, validate_sql


def _run_in_editor(input_data: dict, ctx: dict | None = None) -> str:
    sql = (input_data.get("sql") or "").strip()
    if not sql:
        return json.dumps({"error": "Missing required `sql`."})
    try:
        validate_sql(sql)
    except SQLValidationError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "status": "queued",
        "sql": sql,
        "message": "SQL placed in the user's editor and will run automatically.",
    })
