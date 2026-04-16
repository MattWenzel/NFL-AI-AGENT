"""Player search tool: name/position/team filter against the players table."""

from agent.tools.sql_sandbox import execute_safe_sql
from agent.tools._helpers import _truncate_rows


def _search_players(input_data: dict) -> str:
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
    return _truncate_rows(result.rows, "data")
