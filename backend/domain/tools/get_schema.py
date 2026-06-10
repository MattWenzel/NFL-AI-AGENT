"""Schema discovery tool: the agent-facing wrapper.

The actual introspection + projection logic lives in
`backend.domain.tools.sandbox.schema`. This module is just the tool
handler that translates the canonical schema response into a JSON string
for the agent.
"""

import json
import logging

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox.schema import (
    build_schema_response,
    build_table_schema,
)

logger = logging.getLogger(__name__)


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


TOOL = Tool(
    name="get_schema",
    description=(
        "Get the schema (columns, types, join edges) for a specific table. "
        "CALL THIS BEFORE querying pfr_advanced, ngs_stats, qbr, combine, or draft_picks for the first time in a "
        "conversation — these tables use abbreviated or domain-specific column names (e.g. pfr_advanced's rush/rec "
        "stat_types use 'att', 'yds', 'ybc', 'brk_tkl' not 'attempts' / 'rushing_yards' / 'yards_before_contact'; "
        "qbr uses ESPN naming like 'qbr_total', 'pts_added', 'qb_plays') and guessing produces 'no such column' "
        "errors that waste a tool iteration. Also call it immediately after any column-name error. Skip get_schema "
        "only for well-documented tables whose columns are listed in the system prompt (game_stats, season_stats, "
        "games, play_by_play, players). Issue get_schema in parallel with other independent tool calls to save iterations."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "Table name. Player/reference: players, player_ids, games, stadiums, officials. Player stats: game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, injuries. Team stats: team_game_stats, team_season_stats. Player meta: draft_picks, combine, contracts, contracts_cap_breakdown. Depth charts: v_depth_charts (cross-era view — preferred), depth_charts, depth_charts_2025. Play-by-play: play_by_play, pbp_participation, ftn_charting.",
            },
        },
        "required": ["table_name"],
    },
    handler=_get_schema,
)
