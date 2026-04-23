"""Table aliases + auto-derived join graph.

The join graph used to be a hand-maintained dict. As of 2026-04-23 the
producer repo (NFLVERSE) declares 60 foreign-key constraints on the
DuckDB build and DuckDB enforces them on every INSERT, so the DB is
authoritative for the join graph. We load the edges at import time via
`duckdb_constraints()` — the canonical DuckDB metadata view (the
standard `information_schema.*` has a known bug that reports the child
table as the referenced one, per the NFLVERSE handoff).

One hand-coded supplement: `v_depth_charts` is a UNION view that can't
carry an FK declaration, but the join to `players` via `player_gsis_id`
is still the correct cross-era depth-chart query path.

Collapsing rules for pairs with multiple FKs:
- `play_by_play ↔ players`: 46 role columns (passer_player_id,
  rusher_player_id, …) all FK to `player_gsis_id`. Use `passer_player_id`
  as the representative; the full set is documented in the play_by_play
  guide and available via `get_schema("play_by_play")`.
- `depth_charts_2025 ↔ players`, `draft_picks ↔ players`: two FKs each
  (GSIS + PFR or ESPN). Prefer `player_gsis_id` as the canonical column.
"""

from __future__ import annotations

import logging

import duckdb

from config import DB_PATH

logger = logging.getLogger(__name__)


TABLE_ALIASES: dict[str, str] = {
    "p": "players",
    "gs": "game_stats",
    "ss": "season_stats",
    "g": "games",
    "sc": "snap_counts",
    "ngs": "ngs_stats",
    "dc": "depth_charts",
    "dc25": "depth_charts_2025",
    "vdc": "v_depth_charts",
    "pfr": "pfr_advanced",
    "qbr": "qbr",
    "dp": "draft_picks",
    "c": "combine",
    "pi": "player_ids",
    "pbp": "play_by_play",
}

TABLE_TO_ALIAS: dict[str, str] = {v: k for k, v in TABLE_ALIASES.items()}


# Pair-specific representative-column overrides, keyed on (parent, child).
# Without an override, the first FK (alphabetical on child column) wins and
# multi-column pairs fall back to the canonical-player-column preference
# below. Overrides let us point the LLM at the most-queried column.
_PAIR_REPRESENTATIVE: dict[tuple[str, str], str] = {
    ("players", "play_by_play"): "passer_player_id",
}

# Preferred canonical player-ID columns when a pair has multiple FKs and
# no explicit override above. player_gsis_id is the cross-DB primary;
# PFR is a fallback when GSIS isn't present on the child side.
_CANONICAL_PLAYER_COLUMNS = (
    "player_gsis_id",
    "player_pfr_id",
    "player_espn_id",
)


def _load_join_edges_from_db() -> dict[tuple[str, str], tuple[str, str, bool]]:
    """Query DuckDB FK metadata and build the join graph.

    Keys are `(parent_table, child_table)` — players-side on the left,
    matching the convention the hand-maintained dict used. `cast_needed`
    is always False now (all normalized ID columns are VARCHAR); the tuple
    shape stays for backward compatibility with `_get_joins` in get_schema.
    """
    if not DB_PATH.exists():
        logger.warning(
            "DB_PATH does not exist (%s) — JOIN_EDGES will be empty", DB_PATH
        )
        return {}
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception as exc:
        logger.warning("Could not open DB for FK introspection: %s", exc)
        return {}

    try:
        rows = conn.execute(
            """
            SELECT table_name,
                   constraint_column_names[1] AS column_name,
                   referenced_table,
                   referenced_column_names[1] AS referenced_column
            FROM duckdb_constraints()
            WHERE constraint_type = 'FOREIGN KEY'
            ORDER BY table_name, column_name
            """
        ).fetchall()
    finally:
        conn.close()

    edges: dict[tuple[str, str], tuple[str, str, bool]] = {}

    def _rank(col: str) -> int:
        try:
            return _CANONICAL_PLAYER_COLUMNS.index(col)
        except ValueError:
            return 99

    for child, child_col, parent, parent_col in rows:
        key = (parent, child)

        # Explicit representative for this pair — pick exactly that column.
        override = _PAIR_REPRESENTATIVE.get(key)
        if override is not None:
            if child_col != override:
                continue
            edges[key] = (parent_col, child_col, False)
            continue

        if key not in edges:
            edges[key] = (parent_col, child_col, False)
            continue

        # Pair already has an edge — prefer more-canonical player columns.
        existing_child_col = edges[key][1]
        if _rank(child_col) < _rank(existing_child_col):
            edges[key] = (parent_col, child_col, False)

    return edges


# Views can't carry FK declarations, so their join edges are hand-coded.
# Keep this list small and justified; every entry here is drift risk.
_VIEW_JOIN_EDGES: dict[tuple[str, str], tuple[str, str, bool]] = {
    # v_depth_charts is a UNION over depth_charts + depth_charts_2025.
    # Primary use: cross-era depth-chart queries joined to players via GSIS.
    ("players", "v_depth_charts"): ("player_gsis_id", "player_gsis_id", False),
}


JOIN_EDGES: dict[tuple[str, str], tuple[str, str, bool]] = {
    **_load_join_edges_from_db(),
    **_VIEW_JOIN_EDGES,
}


def _make_bidirectional() -> None:
    """Populate reverse-direction edges so callers can look up (a, b) or (b, a)."""
    additions = {}
    for (a, b), v in JOIN_EDGES.items():
        reverse_key = (b, a)
        if reverse_key not in JOIN_EDGES:
            additions[reverse_key] = (v[1], v[0], v[2])
    JOIN_EDGES.update(additions)


_make_bidirectional()
