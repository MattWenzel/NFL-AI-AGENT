"""CSV export tool: run a query and serialize the result to a downloadable file."""

import csv
import io
import json
import logging
import re
import time

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import execute_export_sql
from backend.config import EXPORTS_DIR, format_file_size

logger = logging.getLogger(__name__)


def _sanitize_filename(name: str) -> str:
    """Sanitize filename: keep alphanumeric, hyphens, underscores only."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip())
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized[:80] or "export"


def _create_csv_export(input_data: dict, ctx: dict | None = None) -> str:
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

    file_size = csv_path.stat().st_size
    size_display = format_file_size(file_size)

    # Register in the CSV library if a store hook was supplied by the
    # runtime; a missing hook means the tool is being invoked outside the
    # runtime, in which case we still return the download info but skip
    # library registration). If the registry write fails we delete the file
    # so the user doesn't end up with an orphaned CSV they can't manage.
    register = ctx.get("register_export") if ctx else None
    if register is not None:
        try:
            register({
                "filename": csv_filename,
                "title": sanitized,
                "sql": sql,
                "row_count": result.row_count,
                "columns": result.columns,
                "file_size": file_size,
            })
        except Exception as exc:
            logger.exception("Failed to register export %s; unlinking file", csv_filename)
            try:
                csv_path.unlink()
            except OSError:
                pass
            return json.dumps({"error": f"Could not save export to library: {exc}"})

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


TOOL = Tool(
    name="create_csv_export",
    description=(
        "Export SQL query results to a downloadable CSV file. "
        "Use this AFTER previewing data with execute_sql and confirming with the user. "
        "Supports up to 10,000 rows with a 30-second timeout. "
        "Returns a download link for the CSV file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "SQL SELECT or WITH statement to export. Must be read-only.",
            },
            "filename": {
                "type": "string",
                "description": "Descriptive filename without extension (e.g. 'qb_passing_stats_2024', 'top_receivers_ppr'). Will be sanitized.",
            },
        },
        "required": ["sql", "filename"],
    },
    handler=_create_csv_export,
)
