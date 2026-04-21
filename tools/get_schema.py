"""Schema discovery tool: table column introspection + join graph.

Static metadata (table aliases, per-table database, join graph) lives
alongside in `schema_metadata.py`.
"""

import json
import sqlite3
from pathlib import Path

from config import DB_PATH, PBP_DB_PATH
from tools.schema_metadata import (
    JOIN_EDGES,
    TABLE_ALIASES,
    TABLE_DATABASE,
    TABLE_TO_ALIAS,
)


TABLE_COLUMNS: dict[str, dict[str, str]] = {}

INTERNAL_TABLES = {"sqlite_sequence"}


def _introspect_db(db_path: Path, schema_prefix: str = "") -> None:
    """Read PRAGMA table_info for all tables in a database."""
    if not db_path.exists():
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        if schema_prefix:
            tables_sql = f"SELECT name FROM {schema_prefix}.sqlite_master WHERE type='table'"
        else:
            tables_sql = "SELECT name FROM sqlite_master WHERE type='table'"
        tables = [row[0] for row in conn.execute(tables_sql).fetchall()
                  if row[0] not in INTERNAL_TABLES]
        for table in tables:
            if schema_prefix:
                pragma_sql = f"PRAGMA {schema_prefix}.table_info({table})"
            else:
                pragma_sql = f"PRAGMA table_info({table})"
            cols = conn.execute(pragma_sql).fetchall()
            TABLE_COLUMNS[table] = {
                row[1]: row[2] if row[2] else "TEXT"
                for row in cols
            }
    finally:
        if conn:
            conn.close()


def _init_columns() -> None:
    _introspect_db(DB_PATH)
    _introspect_db(PBP_DB_PATH)


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
        db = TABLE_DATABASE.get(table_name, "main")
        col_list = [
            {"name": col_name, "type": col_type}
            for col_name, col_type in columns.items()
        ]
        tables[table_name] = {
            "alias": alias,
            "database": db,
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
    db = TABLE_DATABASE.get(table_name, "main")
    col_list = [
        {"name": col_name, "type": col_type}
        for col_name, col_type in columns.items()
    ]
    related_joins = _get_joins(table_name)

    return {
        "name": table_name,
        "alias": alias,
        "database": db,
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
