"""Read-only SQL execution with safeguards for the AI chat agent."""

import logging
import re
import threading
from dataclasses import dataclass

import duckdb

from config import DB_PATH

logger = logging.getLogger(__name__)

# Wall-clock query timeouts. DuckDB has no statement_timeout config, so we
# enforce via threading.Timer + conn.interrupt().
QUERY_TIMEOUT_SECONDS = 30
EXPORT_TIMEOUT_SECONDS = 60
MAX_ROWS = 500
EXPORT_MAX_ROWS = 10_000

# Strip leading whitespace + SQL comments (-- line and /* block */) before
# checking the leading keyword. Done in two steps (strip, then match) so
# `_ALLOWED_START` is a simple keyword check that can't be confused by
# crafted comment payloads.
_LEADING_COMMENT = re.compile(
    r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)+",
    re.DOTALL,
)
_ALLOWED_START = re.compile(r"^(SELECT|WITH)\b", re.IGNORECASE)

# Trailing LIMIT clause with numeric value or parameter placeholder (?, :name).
# Numeric values are capped at max_rows; placeholders are trusted to the caller.
_TRAILING_LIMIT = re.compile(
    r"\bLIMIT\s+(\d+|\?|:\w+)(\s+OFFSET\s+(?:\d+|\?|:\w+))?\s*$",
    re.IGNORECASE,
)

# Strings and comments, used when detecting naked semicolons for multi-statement
# rejection. DuckDB's `execute()` can accept multiple statements; we want to
# reject them at validate-time so a crafted second statement can't sneak past.
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'")
_QUOTED_IDENT = re.compile(r'"(?:[^"\\]|\\.)*"')
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


@dataclass
class SQLResult:
    """Result of a sandboxed SQL query."""
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool


class SQLValidationError(Exception):
    """Raised when SQL fails validation checks."""


def _strip_leading_comments(sql: str) -> str:
    """Repeatedly strip leading whitespace + SQL comments from `sql`.

    Looped because `--` line comments and `/* */` block comments can
    interleave; one regex pass would only consume the first run.
    """
    previous = None
    current = sql.lstrip()
    while previous != current:
        previous = current
        current = _LEADING_COMMENT.sub("", current)
    return current


def _strip_strings_and_comments(sql: str) -> str:
    """Strip string literals, quoted identifiers, and comments.

    Used to detect naked (unquoted) semicolons for multi-statement rejection.
    """
    s = _STRING_LITERAL.sub("", sql)
    s = _QUOTED_IDENT.sub("", s)
    s = _LINE_COMMENT.sub("", s)
    s = _BLOCK_COMMENT.sub("", s)
    return s


def validate_sql(sql: str) -> None:
    """Validate that SQL is a safe read-only statement.

    Raises SQLValidationError if the SQL is not allowed.
    """
    stripped = sql.strip()
    if not stripped:
        raise SQLValidationError("Empty SQL statement")

    # Strip leading comments before checking the keyword so a crafted
    # `/* SELECT */ DELETE …` can't masquerade as a SELECT.
    body = _strip_leading_comments(stripped)
    if not body or not _ALLOWED_START.match(body):
        raise SQLValidationError(
            "Only SELECT and WITH (CTE) statements are allowed"
        )

    # Reject multi-statement. Strings, identifiers, and comments are stripped
    # so a legal query with `;` inside a string literal still passes.
    unquoted = _strip_strings_and_comments(stripped).rstrip().rstrip(";").rstrip()
    if ";" in unquoted:
        raise SQLValidationError(
            "SQL must contain only one statement."
        )


def _run_sql(sql: str, max_rows: int, timeout_seconds: int, params: tuple = ()) -> SQLResult:
    """Core SQL execution with configurable limits.

    - Read-only DuckDB connection
    - Statement validation (SELECT/WITH only, single statement)
    - Query timeout via threading.Timer + conn.interrupt()
    - Configurable row limit and timeout
    """
    validate_sql(sql)

    # Clamp a placeholder `LIMIT ?` param at max_rows so a caller passing a
    # bound value above the cap can't bypass the row limit.
    params = _clamp_limit_param(sql, params, max_rows)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    timer = threading.Timer(timeout_seconds, conn.interrupt)
    timer.start()

    try:
        # Inject row limit if not already present
        sql_with_limit = _ensure_limit(sql, max_rows)

        try:
            if params:
                cursor = conn.execute(sql_with_limit, params)
            else:
                cursor = conn.execute(sql_with_limit)
            rows = cursor.fetchall()
        except duckdb.InterruptException:
            logger.warning("SQL query timed out: %s", sql[:500])
            raise SQLValidationError(
                "Query timed out. Add more filters or simplify the query."
            )
        except duckdb.Error as e:
            raise SQLValidationError(f"SQL error: {e}")

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
        timer.cancel()
        conn.close()


def execute_safe_sql(sql: str, params: tuple = ()) -> SQLResult:
    """Execute a read-only SQL query with standard limits (500 rows, ~30s timeout)."""
    return _run_sql(sql, MAX_ROWS, QUERY_TIMEOUT_SECONDS, params)


def execute_export_sql(sql: str) -> SQLResult:
    """Execute a read-only SQL query with export limits (10,000 rows, ~60s timeout)."""
    return _run_sql(sql, EXPORT_MAX_ROWS, EXPORT_TIMEOUT_SECONDS)


def _ensure_limit(sql: str, max_rows: int) -> str:
    """Add LIMIT clause if missing, or cap an existing numeric LIMIT at max_rows.

    Parameterized LIMITs (`?` or `:name`) are passed through untouched at the
    SQL level — clamping happens at bind time via `_clamp_limit_param`.
    """
    stripped = sql.rstrip().rstrip(";")
    match = _TRAILING_LIMIT.search(stripped)
    if not match:
        return f"{stripped}\nLIMIT {max_rows}"

    value = match.group(1)
    if not value.isdigit():
        # Placeholder LIMIT — the bound value is clamped in _clamp_limit_param.
        return stripped

    existing = int(value)
    if existing > max_rows:
        offset_part = match.group(2) or ""
        return stripped[: match.start()] + f"LIMIT {max_rows}{offset_part}"
    return stripped


def _clamp_limit_param(sql: str, params: tuple, max_rows: int) -> tuple:
    """Clamp a placeholder `LIMIT ?` param at `max_rows`.

    The SQL regex already detects a trailing `LIMIT ?` (optionally followed
    by `OFFSET ?`); this helper locates the corresponding positional param
    and clamps it. OFFSET is left alone — it isn't bounded by row caps.

    Only handles `?` placeholders. Named placeholders (`:name`) would use a
    dict rather than a tuple; the codebase doesn't mix styles.
    """
    if not params:
        return params
    stripped = sql.rstrip().rstrip(";")
    match = _TRAILING_LIMIT.search(stripped)
    if not match:
        return params
    limit_value = match.group(1)
    if limit_value != "?":
        return params  # numeric or `:name` — no tuple clamp applies
    # Count `?` placeholders before the LIMIT clause to find its positional index.
    before = _strip_strings_and_comments(stripped[: match.start()])
    limit_idx = before.count("?")
    if limit_idx >= len(params):
        return params  # misaligned — let DuckDB raise the real error
    try:
        current = params[limit_idx]
    except IndexError:
        return params
    if isinstance(current, int) and current > max_rows:
        new_params = list(params)
        new_params[limit_idx] = max_rows
        return tuple(new_params)
    return params
