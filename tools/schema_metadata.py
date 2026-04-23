"""Static schema metadata: table aliases and the bidirectional join graph.
Separated from `get_schema.py` so the handler file focuses on introspection
logic.

After the 2026-04-23 ID-normalization pass, `players` carries direct
`player_gsis_id` / `player_pfr_id` / `player_espn_id` columns, so most
supplementary tables (snap_counts, pfr_advanced, qbr, combine) no longer
need the `player_ids` bridge. The bridge is still listed for situations
where the caller has a non-canonical ID (yahoo_id, sleeper_id, fantasy_id)
and wants to resolve it.
"""

from __future__ import annotations


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

# Join graph: (table_a, table_b) -> (a_col, b_col, cast_needed). Populated
# bidirectionally at import time so callers can look up an edge in either
# direction. With the normalized ID columns, `cast_needed` is no longer
# True on any edge — kept in the tuple shape for backwards compatibility.
JOIN_EDGES: dict[tuple[str, str], tuple[str, str, bool]] = {
    # Direct player_gsis_id joins
    ("players", "game_stats"): ("player_gsis_id", "player_gsis_id", False),
    ("players", "season_stats"): ("player_gsis_id", "player_gsis_id", False),
    ("players", "ngs_stats"): ("player_gsis_id", "player_gsis_id", False),
    ("players", "depth_charts"): ("player_gsis_id", "player_gsis_id", False),
    ("players", "depth_charts_2025"): ("player_gsis_id", "player_gsis_id", False),
    # v_depth_charts is a UNION view over depth_charts + depth_charts_2025 with
    # a normalized 12-column schema. Prefer this for cross-era queries; the
    # base tables remain available for era-specific columns (elias_id,
    # first_name, last_name on legacy; pos_grp_id / pos_slot on 2025).
    ("players", "v_depth_charts"): ("player_gsis_id", "player_gsis_id", False),
    ("players", "draft_picks"): ("player_gsis_id", "player_gsis_id", False),
    # Direct player_pfr_id joins (previously required player_ids bridge)
    ("players", "snap_counts"): ("player_pfr_id", "player_pfr_id", False),
    ("players", "pfr_advanced"): ("player_pfr_id", "player_pfr_id", False),
    ("players", "combine"): ("player_pfr_id", "player_pfr_id", False),
    # Direct player_espn_id join (previously required bridge + CAST)
    ("players", "qbr"): ("player_espn_id", "player_espn_id", False),
    # player_ids bridge — still useful when the caller has a non-canonical
    # ID (yahoo_id, sleeper_id, etc.) and wants the GSIS profile.
    ("players", "player_ids"): ("player_gsis_id", "gsis_id", False),
    # Game-level joins
    ("game_stats", "games"): ("game_id", "game_id", False),
    ("games", "snap_counts"): ("game_id", "game_id", False),
    ("games", "depth_charts"): ("season", "season", False),  # + week
    # play_by_play joins
    ("play_by_play", "games"): ("game_id", "game_id", False),
}


def _make_bidirectional() -> None:
    additions = {}
    for (a, b), v in JOIN_EDGES.items():
        reverse_key = (b, a)
        if reverse_key not in JOIN_EDGES:
            additions[reverse_key] = (v[1], v[0], v[2])
    JOIN_EDGES.update(additions)


_make_bidirectional()
