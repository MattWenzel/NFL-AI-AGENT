"""Declarative tool schemas (Anthropic tool_use format) + typed exports."""

from agent.providers.base import ToolDefinition

TOOL_DEFINITIONS = [
    {
        "name": "search_players",
        "description": "Search for NFL players by name, position, or team. Use this FIRST whenever a user mentions a player name to resolve their gsis_id for subsequent queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Player name to search (partial match supported, e.g. 'Mahomes', 'Patrick Mahomes')",
                },
                "position": {
                    "type": "string",
                    "description": "Filter by position (QB, RB, WR, TE, etc.)",
                },
                "team": {
                    "type": "string",
                    "description": "Filter by current team abbreviation (KC, BUF, etc.)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10)",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query against the SQLite database. "
            "Use this for all data queries: joins, aggregation, window functions, CTEs, UNION, subqueries. "
            "The query runs in a sandboxed read-only connection with a 10-second timeout and 500-row limit. "
            "For play-by-play data, reference the table as play_by_play (it auto-attaches pbp.db)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement. Must be read-only (no INSERT/UPDATE/DELETE/DROP).",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "get_schema",
        "description": "Get the schema (columns, types, join edges) for a specific table. Use this when you need exact column names or want to explore what data is available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name: players, player_ids, game_stats, season_stats, games, draft_picks, combine, snap_counts, ngs_stats, depth_charts, depth_charts_2025, pfr_advanced, qbr, play_by_play",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_player_info",
        "description": "Get detailed player biography and cross-platform IDs for a specific player. Requires gsis_id (use search_players first to find it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "gsis_id": {
                    "type": "string",
                    "description": "Player's GSIS ID (e.g. '00-0033873' for Patrick Mahomes)",
                },
            },
            "required": ["gsis_id"],
        },
    },
    {
        "name": "create_csv_export",
        "description": (
            "Export SQL query results to a downloadable CSV file. "
            "Use this AFTER previewing data with execute_sql and confirming with the user. "
            "Supports up to 10,000 rows with a 30-second timeout. "
            "Returns a download link for the CSV file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement to export. Must be read-only.",
                },
                "filename": {
                    "type": "string",
                    "description": "Descriptive filename without extension (e.g. 'qb_passing_stats_2024', 'top_receivers_ppr'). Will be sanitized.",
                },
            },
            "required": ["sql", "filename"],
        },
    },
]

# Typed tool definitions — preferred import for consumers
TOOLS: list[ToolDefinition] = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]
