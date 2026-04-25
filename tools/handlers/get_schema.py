"""Schema discovery tool: table column introspection + join graph.

Static metadata (table aliases, FK-derived join graph) lives alongside
in `schema_metadata.py`.
"""

import json
import logging
from pathlib import Path

import duckdb

from config import DB_PATH
from tools.sandbox.schema_metadata import (
    JOIN_EDGES,
    TABLE_ALIASES,
    TABLE_TO_ALIAS,
)

logger = logging.getLogger(__name__)


TABLE_COLUMNS: dict[str, dict[str, str]] = {}


def _introspect_db(db_path: Path) -> None:
    """Read table/column metadata from a DuckDB file via information_schema.

    If the file is missing or locked (e.g. a concurrent build script on
    the NFLVERSE side holds the lock — DuckDB refuses cross-process
    access even in read-only mode), log a warning and leave TABLE_COLUMNS
    empty. Callers degrade gracefully: `get_schema` tool returns "Unknown
    table" until the lock is released and the process restarts. Matches
    the resilience pattern in `schema_metadata._load_join_edges_from_db`.
    """
    if not db_path.exists():
        return
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        logger.warning(
            "Could not open DB for schema introspection (continuing with empty table map): %s",
            exc,
        )
        return
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' "
                "AND table_type IN ('BASE TABLE', 'VIEW') "
                "ORDER BY table_name"
            ).fetchall()
        ]
        for table in tables:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? "
                "ORDER BY ordinal_position",
                [table],
            ).fetchall()
            TABLE_COLUMNS[table] = {
                row[0]: row[1] if row[1] else "TEXT"
                for row in cols
            }
    finally:
        conn.close()


def _init_columns() -> None:
    _introspect_db(DB_PATH)


_init_columns()


def _get_joins(table_name: str | None = None) -> list[dict]:
    """Return deduplicated join edges, optionally filtered to a single table.

    Each dict has keys: table_a, table_b, column_a, column_b, cast_needed.
    """
    joins = []
    seen: set[tuple[str, str]] = set()
    for (a, b), (a_col, b_col, cast_needed) in JOIN_EDGES.items():
        if table_name and a != table_name and b != table_name:
            continue
        pair = tuple(sorted([a, b]))
        if pair in seen:
            continue
        seen.add(pair)
        if (a_col and "||" in a_col) or (b_col and "||" in b_col):
            continue
        joins.append({
            "table_a": a,
            "table_b": b,
            "column_a": a_col,
            "column_b": b_col,
            "cast_needed": cast_needed,
        })
    return joins


def build_schema_response() -> dict:
    """Build the full schema response covering all introspected tables."""
    tables = {}
    for table_name, columns in TABLE_COLUMNS.items():
        alias = TABLE_TO_ALIAS.get(table_name)
        col_list = [
            {"name": col_name, "type": col_type}
            for col_name, col_type in columns.items()
        ]
        tables[table_name] = {
            "alias": alias,
            "columns": col_list,
            "column_count": len(col_list),
        }

    joins = []
    for j in _get_joins():
        join_info = {
            "table_a": j["table_a"],
            "table_b": j["table_b"],
            "column_a": j["column_a"],
            "column_b": j["column_b"],
        }
        if j["cast_needed"]:
            join_info["note"] = "CAST required for type matching"
        joins.append(join_info)

    return {
        "tables": tables,
        "joins": joins,
        "aliases": TABLE_ALIASES,
        "total_tables": len(tables),
    }


def build_table_schema(table_name: str) -> dict | None:
    """Build schema for a single table."""
    if table_name not in TABLE_COLUMNS:
        return None
    columns = TABLE_COLUMNS[table_name]
    alias = TABLE_TO_ALIAS.get(table_name)
    col_list = [
        {"name": col_name, "type": col_type}
        for col_name, col_type in columns.items()
    ]
    related_joins = _get_joins(table_name)

    return {
        "name": table_name,
        "alias": alias,
        "columns": col_list,
        "column_count": len(col_list),
        "joins": related_joins,
    }


def _get_schema(input_data: dict, ctx: dict | None = None) -> str:
    table_name = input_data.get("table_name", "")
    if table_name:
        result = build_table_schema(table_name)
        if result is None:
            return json.dumps({"error": f"Unknown table: {table_name}"})
    else:
        result = build_schema_response()
    # Schema responses are structured JSON; character-level truncation corrupts
    # them. Return the full payload — callers treat schema as reference data.
    return json.dumps(result)
