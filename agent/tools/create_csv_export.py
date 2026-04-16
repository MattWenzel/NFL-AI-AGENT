"""CSV export tool: run a query and serialize the result to a downloadable file."""

import csv
import io
import json
import re
import time

from agent.tools.sql_sandbox import execute_export_sql
from config import EXPORTS_DIR, format_file_size


def _sanitize_filename(name: str) -> str:
    """Sanitize filename: keep alphanumeric, hyphens, underscores only."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:80] or "export"


def _create_csv_export(input_data: dict) -> str:
    sql = input_data.get("sql", "")
    raw_filename = input_data.get("filename", "export")

    result = execute_export_sql(sql)

    if not result.rows:
        return json.dumps({"error": "Query returned no rows. Adjust filters and try again."})

    sanitized = _sanitize_filename(raw_filename)
    timestamp = int(time.time())
    csv_filename = f"{sanitized}_{timestamp}.csv"

    EXPORTS_DIR.mkdir(exist_ok=True)
    csv_path = EXPORTS_DIR / csv_filename

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=result.columns)
    writer.writeheader()
    writer.writerows(result.rows)
    csv_path.write_text(buf.getvalue(), encoding="utf-8")

    size_display = format_file_size(csv_path.stat().st_size)

    output = {
        "download_url": f"/exports/{csv_filename}",
        "filename": csv_filename,
        "row_count": result.row_count,
        "columns": result.columns,
        "file_size": size_display,
    }
    if result.truncated:
        output["note"] = f"Results truncated to {result.row_count} rows (export limit)"
    return json.dumps(output)
