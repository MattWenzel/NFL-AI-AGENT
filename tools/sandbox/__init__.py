"""DuckDB execution sandbox + canonical schema metadata.

`runner.py` owns read-only SQL execution against the nflverse DuckDB
file: validation, timeouts, row-cap, multi-statement rejection, and
result formatting. `schema_metadata.py` carries the canonical join
edges, table aliases, and database routing used to assemble the schema
the agent sees.

Public surface re-exported here for convenience — most callers only
need `execute_safe_sql`, `execute_export_sql`, and the metadata
constants.
"""

from tools.sandbox.runner import (
    EXPORT_MAX_ROWS,
    EXPORT_TIMEOUT_SECONDS,
    MAX_ROWS,
    QUERY_TIMEOUT_SECONDS,
    _clamp_limit_param,
    _ensure_limit,
    execute_export_sql,
    execute_safe_sql,
    validate_sql,
)

__all__ = [
    "EXPORT_MAX_ROWS",
    "EXPORT_TIMEOUT_SECONDS",
    "MAX_ROWS",
    "QUERY_TIMEOUT_SECONDS",
    "_clamp_limit_param",
    "_ensure_limit",
    "execute_export_sql",
    "execute_safe_sql",
    "validate_sql",
]
