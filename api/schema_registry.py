"""Central schema registry: introspects databases, defines join graph, table aliases."""

import sqlite3
from pathlib import Path

from config import DB_PATH, PBP_DB_PATH

# ---------------------------------------------------------------------------
# Table aliases (short names for the query API)
# ---------------------------------------------------------------------------

TABLE_ALIASES = {
    "p": "players",
    "gs": "game_stats",
    "ss": "season_stats",
    "g": "games",
    "sc": "snap_counts",
    "ngs": "ngs_stats",
    "dc": "depth_charts",
    "dc25": "depth_charts_2025",
    "pfr": "pfr_advanced",
    "qbr": "qbr",
    "dp": "draft_picks",
    "c": "combine",
    "pi": "player_ids",
    "pbp": "play_by_play",
}

ALIAS_TO_TABLE = TABLE_ALIASES
TABLE_TO_ALIAS = {v: k for k, v in TABLE_ALIASES.items()}

# Which database each table lives in
TABLE_DATABASE = {
    "players": "main",
    "game_stats": "main",
    "season_stats": "main",
    "games": "main",
    "snap_counts": "main",
    "ngs_stats": "main",
    "depth_charts": "main",
    "pfr_advanced": "main",
    "qbr": "main",
    "draft_picks": "main",
    "combine": "main",
    "player_ids": "main",
    "play_by_play": "pbp",
    "depth_charts_2025": "main",
}

# ---------------------------------------------------------------------------
# Join graph: adjacency list of (table_a, table_b) -> ON clause
# Each edge is bidirectional.  Bridge tables (player_ids) are intermediaries.
# ---------------------------------------------------------------------------

# Join edges: (table_a, table_b) -> (a_col, b_col, cast_needed)
JOIN_EDGES: dict[tuple[str, str], tuple[str, str, bool]] = {
    # GSIS ID direct joins
    ("players", "game_stats"): ("gsis_id", "player_id", False),
    ("players", "season_stats"): ("gsis_id", "player_id", False),
    ("players", "player_ids"): ("gsis_id", "gsis_id", False),
    ("players", "ngs_stats"): ("gsis_id", "player_gsis_id", False),
    ("players", "depth_charts"): ("gsis_id", "gsis_id", False),
    ("players", "depth_charts_2025"): ("gsis_id", "gsis_id", False),
    ("players", "draft_picks"): ("gsis_id", "gsis_id", False),
    # player_ids bridges
    ("player_ids", "snap_counts"): ("pfr_id", "pfr_player_id", False),
    ("player_ids", "pfr_advanced"): ("pfr_id", "pfr_id", False),
    ("player_ids", "qbr"): ("espn_id", "player_id", True),  # CAST needed
    # Game-level joins
    ("game_stats", "games"): ("game_id", "game_id", False),
    ("games", "snap_counts"): ("game_id", "game_id", False),
    ("games", "depth_charts"): ("season", "season", False),  # + week
    # play_by_play joins (cross-database)
    ("play_by_play", "games"): ("game_id", "game_id", False),
}


def _make_bidirectional():
    """Ensure all edges are accessible in both directions."""
    additions = {}
    for (a, b), v in JOIN_EDGES.items():
        reverse_key = (b, a)
        if reverse_key not in JOIN_EDGES:
            # Swap column order
            additions[reverse_key] = (v[1], v[0], v[2])
    JOIN_EDGES.update(additions)


_make_bidirectional()

# Adjacency list for BFS
JOIN_GRAPH: dict[str, set[str]] = {}
for (a, b) in JOIN_EDGES:
    JOIN_GRAPH.setdefault(a, set()).add(b)
    JOIN_GRAPH.setdefault(b, set()).add(a)

# ---------------------------------------------------------------------------
# SQL reserved words that need quoting
# ---------------------------------------------------------------------------

SQL_RESERVED = {
    "pass", "int", "drop", "rank", "order", "group", "select", "from",
    "where", "join", "on", "as", "and", "or", "not", "in", "like",
    "between", "is", "null", "desc", "asc", "limit", "offset",
    "having", "by", "table", "index", "create", "insert", "update",
    "delete", "set", "values", "into", "check", "default", "key",
    "primary", "foreign", "references", "unique", "constraint",
    "run", "sack", "penalty",
}

# ---------------------------------------------------------------------------
# Column whitelist: introspected at import time
# ---------------------------------------------------------------------------

# {table_name: {col_name: col_type}}
TABLE_COLUMNS: dict[str, dict[str, str]] = {}


INTERNAL_TABLES = {"sqlite_sequence"}


def _introspect_db(db_path: Path, schema_prefix: str = ""):
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


def _init_columns():
    """Introspect both databases on import."""
    _introspect_db(DB_PATH)
    _introspect_db(PBP_DB_PATH)


_init_columns()

ALL_TABLES = set(TABLE_COLUMNS.keys())

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_table_name(name: str) -> str | None:
    """Resolve an alias or full table name. Returns None if invalid."""
    if name in ALL_TABLES:
        return name
    if name in ALIAS_TO_TABLE:
        resolved = ALIAS_TO_TABLE[name]
        if resolved in ALL_TABLES:
            return resolved
    return None


def quote_column(col: str) -> str:
    """Quote a column name if it's a SQL reserved word."""
    if col.lower() in SQL_RESERVED:
        return f'"{col}"'
    return col


def get_join_condition(table_a: str, table_b: str) -> tuple[str, str, bool] | None:
    """Get join columns for two tables. Returns (a_col, b_col, needs_cast) or None."""
    key = (table_a, table_b)
    return JOIN_EDGES.get(key)


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
    """Build the full schema response for GET /schema."""
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

    # Build join relationships
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

    # Find joins involving this table
    related_joins = _get_joins(table_name)

    return {
        "name": table_name,
        "alias": alias,
        "database": db,
        "columns": col_list,
        "column_count": len(col_list),
        "joins": related_joins,
    }
