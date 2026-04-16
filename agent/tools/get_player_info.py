"""Player detail lookup: bio row + cross-platform IDs for a given gsis_id."""

import json

from agent.tools.sql_sandbox import execute_safe_sql
from agent.tools._helpers import _truncate


def _get_player_info(input_data: dict) -> str:
    gsis_id = input_data.get("gsis_id", "")
    result = {}

    try:
        bio = execute_safe_sql(
            "SELECT * FROM players WHERE gsis_id = ? LIMIT 1", (gsis_id,)
        )
        if bio.rows:
            result["player"] = bio.rows[0]
        else:
            return json.dumps({"error": f"No player found with gsis_id '{gsis_id}'"})
    except Exception as e:
        return json.dumps({"error": f"Player lookup failed: {e}"})

    try:
        ids = execute_safe_sql(
            "SELECT * FROM player_ids WHERE gsis_id = ? LIMIT 1", (gsis_id,)
        )
        if ids.rows:
            result["ids"] = ids.rows[0]
        else:
            result["ids_note"] = "No cross-platform IDs found"
    except Exception as e:
        result["ids_note"] = f"Could not fetch cross-platform IDs: {e}"

    return _truncate(json.dumps(result))
