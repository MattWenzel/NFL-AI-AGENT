"""Read-only SQL execution with safeguards for the AI chat agent."""

import logging
import re
import sqlite3
from dataclasses import dataclass

from config import DB_PATH, PBP_DB_PATH

logger = logging.getLogger(__name__)

# Abort query after this many SQLite VM instructions (~30 seconds)
QUERY_TIMEOUT_OPS = 300_000_000
EXPORT_TIMEOUT_OPS = 600_000_000  # ~60 seconds for exports
MAX_ROWS = 500
EXPORT_MAX_ROWS = 10_000

# Patterns that indicate PBP table usage
_PBP_PATTERNS = re.compile(r"\bplay_by_play\b|\bpbp\.", re.IGNORECASE)

# Only allow SELECT and WITH (CTE) statements, tolerating leading whitespace
# and SQL comments (-- line and /* block */) before the keyword.
_ALLOWED_START = re.compile(
    r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*(SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)

# Trailing LIMIT clause with numeric value or parameter placeholder (?, :name).
# Numeric values are capped at max_rows; placeholders are trusted to the caller.
_TRAILING_LIMIT = re.compile(
    r"\bLIMIT\s+(\d+|\?|:\w+)(\s+OFFSET\s+(?:\d+|\?|:\w+))?\s*$",
    re.IGNORECASE,
)


@dataclass
class SQLResult:
    """Result of a sandboxed SQL query."""
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool


class SQLValidationError(Exception):
    """Raised when SQL fails validation checks."""


def validate_sql(sql: str) -> None:
    """Validate that SQL is a safe read-only statement.

    Raises SQLValidationError if the SQL is not allowed.
    """
    stripped = sql.strip()
    if not stripped:
        raise SQLValidationError("Empty SQL statement")

    if not _ALLOWED_START.match(stripped):
        raise SQLValidationError(
            "Only SELECT and WITH (CTE) statements are allowed"
        )


def _run_sql(sql: str, max_rows: int, timeout_ops: int, params: tuple = ()) -> SQLResult:
    """Core SQL execution with configurable limits.

    - Read-only connection (driver-level enforcement)
    - Statement validation (SELECT/WITH only, single statement)
    - Query timeout via progress handler
    - Configurable row limit and timeout
    - Auto-attaches pbp.db when query references play_by_play
    - Optional params tuple for parameterized queries
    """
    validate_sql(sql)

    needs_pbp = bool(_PBP_PATTERNS.search(sql))

    # Open read-only connection
    conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False
    )

    def _timeout_handler():
        return 1  # non-zero aborts the query

    try:
        if needs_pbp:
            if not PBP_DB_PATH.exists():
                raise SQLValidationError(
                    "Play-by-play database (pbp.db) not found"
                )
            logger.debug("Auto-attaching PBP database: %s", PBP_DB_PATH)
            conn.execute(
                "ATTACH DATABASE ? AS pbp", (f"file:{PBP_DB_PATH}?mode=ro",)
            )

        # Inject row limit if not already present
        sql_with_limit = _ensure_limit(sql, max_rows)

        conn.set_progress_handler(_timeout_handler, timeout_ops)
        try:
            cursor = conn.execute(sql_with_limit, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                logger.warning("SQL query timed out: %s", sql[:500])
                raise SQLValidationError(
                    "Query timed out. Add more filters or simplify the query."
                )
            raise SQLValidationError(f"SQL error: {e}")
        except sqlite3.ProgrammingError as e:
            # Raised for binding mismatches
            raise SQLValidationError(f"SQL error: {e}")
        except sqlite3.Warning as e:
            # Raised for multi-statement execute() calls
            raise SQLValidationError(f"SQL error: {e}")
        finally:
            conn.set_progress_handler(None, 0)

        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        truncated = len(rows) >= max_rows
        data = [dict(zip(columns, row)) for row in rows]

        return SQLResult(
            columns=columns,
            rows=data,
            row_count=len(data),
            truncated=truncated,
        )
    finally:
        if needs_pbp:
            try:
                conn.execute("DETACH DATABASE pbp")
            except Exception:
                pass
        conn.close()


def execute_safe_sql(sql: str, params: tuple = ()) -> SQLResult:
    """Execute a read-only SQL query with standard limits (500 rows, ~30s timeout)."""
    return _run_sql(sql, MAX_ROWS, QUERY_TIMEOUT_OPS, params)


def execute_export_sql(sql: str) -> SQLResult:
    """Execute a read-only SQL query with export limits (10,000 rows, ~60s timeout)."""
    return _run_sql(sql, EXPORT_MAX_ROWS, EXPORT_TIMEOUT_OPS)


def _ensure_limit(sql: str, max_rows: int) -> str:
    """Add LIMIT clause if missing, or cap an existing numeric LIMIT at max_rows.

    Parameterized LIMITs (? or :name) are passed through unchanged — the caller
    is responsible for clamping the bound value.
    """
    stripped = sql.rstrip().rstrip(";")
    match = _TRAILING_LIMIT.search(stripped)
    if not match:
        return f"{stripped}\nLIMIT {max_rows}"

    value = match.group(1)
    if not value.isdigit():
        # Placeholder LIMIT — trust the caller, don't duplicate.
        return stripped

    existing = int(value)
    if existing > max_rows:
        offset_part = match.group(2) or ""
        return stripped[: match.start()] + f"LIMIT {max_rows}{offset_part}"
    return stripped
