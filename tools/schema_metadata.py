"""Static schema metadata: table aliases and the bidirectional join graph.
Separated from `get_schema.py` so the handler file focuses on introspection
logic.
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
# direction.
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
