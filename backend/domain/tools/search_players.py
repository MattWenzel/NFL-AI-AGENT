"""Player name search — resolves display names to player_gsis_id."""

from backend.domain.providers.types import Tool
from backend.domain.tools.sandbox import execute_safe_sql
from backend.domain.tools.truncate import truncate_rows


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
    sql = f"SELECT player_gsis_id, display_name, position, latest_team FROM players{where} LIMIT ?"

    result = execute_safe_sql(sql, tuple(params) + (limit,))
    return truncate_rows(result.rows, "data")


TOOL = Tool(
    name="search_players",
    description=(
        "Search for NFL players by name. Use this FIRST whenever a user mentions a "
        "player name to resolve their player_gsis_id for subsequent queries. For "
        "position/team filtering, use execute_sql against the players table."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Player name to search (partial match supported, e.g. 'Mahomes', 'Patrick Mahomes')",
            },
        },
        "required": ["name"],
    },
    handler=_search_players,
)
