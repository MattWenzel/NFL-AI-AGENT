"""Declarative tool schemas (Anthropic tool_use format) + typed exports."""

from provider.base import ToolDefinition

TOOL_DEFINITIONS = [
    {
        "name": "search_players",
        "description": "Search for NFL players by name. Use this FIRST whenever a user mentions a player name to resolve their gsis_id for subsequent queries. For position/team filtering, use execute_sql against the players table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Player name to search (partial match supported, e.g. 'Mahomes', 'Patrick Mahomes')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a read-only SQL query against the SQLite database. "
            "Use this for all data queries: joins, aggregation, window functions, CTEs, UNION, subqueries. "
            "BEFORE querying pfr_advanced, ngs_stats, qbr, combine, or draft_picks for the first time in a conversation, "
            "call get_schema to see the exact column names — these tables use abbreviated or domain-specific naming "
            "that is not reliably memorized in the system prompt, and guessing produces 'no such column' errors. "
            "The query runs in a sandboxed read-only connection with a 30-second timeout and 500-row limit. "
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
        "name": "get_guide",
        "description": (
            "Load a topic-specific guide for writing database queries. Each guide has "
            "column references, gotchas, and ready-to-copy SQL templates. Call BEFORE "
            "writing SQL for the topic. Topics: fantasy (scoring rules + kicker formula), "
            "player_stats (season/game stats + snap_counts + NGS + PFR + QBR column catalogs), "
            "play_by_play (372-col PBP reference + query patterns), "
            "drives (drive-level aggregations, TOP parsing, scoring-drive rates), "
            "postseason (playoff encoding across tables, Super Bowl/WC/DIV/CON queries), "
            "player_profile (IDs, draft, combine, depth charts), "
            "games (schedules + game-level joins + betting lines)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": [
                        "fantasy",
                        "player_stats",
                        "play_by_play",
                        "drives",
                        "postseason",
                        "player_profile",
                        "games",
                    ],
                    "description": "Which guide to load.",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "get_schema",
        "description": (
            "Get the schema (columns, types, join edges) for a specific table. "
            "CALL THIS BEFORE querying pfr_advanced, ngs_stats, qbr, combine, or draft_picks for the first time in a "
            "conversation — these tables use abbreviated or domain-specific column names (e.g. pfr_advanced's rush/rec "
            "stat_types use 'att', 'yds', 'ybc', 'brk_tkl' not 'attempts' / 'rushing_yards' / 'yards_before_contact'; "
            "qbr uses ESPN naming like 'qbr_total', 'pts_added', 'qb_plays') and guessing produces 'no such column' "
            "errors that waste a tool iteration. Also call it immediately after any column-name error. Skip get_schema "
            "only for well-documented tables whose columns are listed in the system prompt (game_stats, season_stats, "
            "games, play_by_play, players). Issue get_schema in parallel with other independent tool calls to save iterations."
        ),
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
    {
        "name": "create_chart",
        "description": (
            "Generate a chart from a SQL query. Use when a visual comparison answers the question "
            "better than prose — side-by-side player stats, yearly trends, category breakdowns. "
            "The chart renders inline in your response. Prefer execute_sql for tabular answers; "
            "use create_chart when the user would benefit from seeing the shape of the data. "
            "Keep rows aggregated (GROUP BY, LIMIT 10-50) so the chart stays readable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH returning the rows to plot. Same rules as execute_sql (read-only).",
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "scatter", "pie"],
                    "description": "bar=category comparison, line=trend, scatter=correlation, pie=composition",
                },
                "x": {
                    "type": "string",
                    "description": "Column name for the X axis / category labels (must exist in the SELECT)",
                },
                "y": {
                    "type": "string",
                    "description": "Column name for the Y axis / values (must exist in the SELECT, should be numeric)",
                },
                "group_by": {
                    "type": "string",
                    "description": "Optional: column to split into multiple series (bar/line only)",
                },
                "title": {
                    "type": "string",
                    "description": "Short chart title shown above the rendered chart",
                },
            },
            "required": ["sql", "chart_type", "x", "y"],
        },
    },
]

# Typed tool definitions — preferred import for consumers
TOOLS: list[ToolDefinition] = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]
