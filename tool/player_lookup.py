"""Player lookup tools: fuzzy search + single-player detail fetch."""

import json

from tool.sandbox import execute_safe_sql
from tool.truncate import truncate_text, truncate_rows


def _search_players(input_data: dict, ctx: dict | None = None) -> str:
    conditions = []
    params = []
    if input_data.get("name"):
        conditions.append("display_name LIKE ?")
        params.append(f"%{input_data['name']}%")
    if input_data.get("position"):
        conditions.append("position = ?")
        params.append(input_data["position"])
    if input_data.get("team"):
        conditions.append("latest_team = ?")
        params.append(input_data["team"])
    try:
        raw_limit = int(input_data.get("limit", 10))
    except (ValueError, TypeError):
        raw_limit = 10
    limit = max(1, min(raw_limit, 50))

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT gsis_id, display_name, position, latest_team FROM players{where} LIMIT ?"

    result = execute_safe_sql(sql, tuple(params) + (limit,))
    return truncate_rows(result.rows, "data")


def _get_player_info(input_data: dict, ctx: dict | None = None) -> str:
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

    return truncate_text(json.dumps(result))
