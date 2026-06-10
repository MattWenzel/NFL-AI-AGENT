"""Single-player detail fetch: biography + cross-platform IDs."""

import json

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import execute_safe_sql
from backend.domain.tools.truncate import truncate_text


def _get_player_info(input_data: dict, ctx: dict | None = None) -> str:
    player_gsis_id = input_data.get("player_gsis_id", "")
    result = {}

    try:
        bio = execute_safe_sql(
            "SELECT * FROM players WHERE player_gsis_id = ? LIMIT 1", (player_gsis_id,)
        )
        if bio.rows:
            result["player"] = bio.rows[0]
        else:
            return json.dumps({"error": f"No player found with player_gsis_id '{player_gsis_id}'"})
    except Exception as e:
        return json.dumps({"error": f"Player lookup failed: {e}"})

    try:
        ids = execute_safe_sql(
            "SELECT * FROM player_ids WHERE gsis_id = ? LIMIT 1", (player_gsis_id,)
        )
        if ids.rows:
            result["ids"] = ids.rows[0]
        else:
            result["ids_note"] = "No cross-platform IDs found"
    except Exception as e:
        result["ids_note"] = f"Could not fetch cross-platform IDs: {e}"

    return truncate_text(json.dumps(result))


TOOL = Tool(
    name="get_player_info",
    description=(
        "Get detailed player biography and cross-platform IDs for a specific player. "
        "Requires player_gsis_id (use search_players first to find it)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "player_gsis_id": {
                "type": "string",
                "description": "Player's GSIS ID (e.g. '00-0033873' for Patrick Mahomes)",
            },
        },
        "required": ["player_gsis_id"],
    },
    handler=_get_player_info,
)
