"""Declarative tool schemas (Anthropic tool_use format) + typed exports."""

from backend.domain.providers.types import ToolDefinition
from backend.domain.tools.guide_registry import GUIDE_TOPICS

TOOL_DEFINITIONS = [
    {
        "name": "search_players",
        "description": "Search for NFL players by name. Use this FIRST whenever a user mentions a player name to resolve their player_gsis_id for subsequent queries. For position/team filtering, use execute_sql against the players table.",
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
            "Execute a read-only SQL query against the DuckDB database. "
            "Use this for all data queries: joins, aggregation, window functions, CTEs, UNION, subqueries. "
            "BEFORE querying pfr_advanced, ngs_stats, qbr, combine, or draft_picks for the first time in a conversation, "
            "call get_schema to see the exact column names — these tables use abbreviated or domain-specific naming "
            "that is not reliably memorized in the system prompt, and guessing produces 'no such column' errors. "
            "The query runs in a sandboxed read-only connection with a 30-second timeout and 500-row limit."
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
                    "enum": list(GUIDE_TOPICS),
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
                    "description": "Table name. Player/reference: players, player_ids, games, stadiums, officials. Player stats: game_stats, season_stats, weekly_rosters, snap_counts, ngs_stats, pfr_advanced, pfr_advanced_weekly, qbr, injuries. Team stats: team_game_stats, team_season_stats. Player meta: draft_picks, combine, contracts, contracts_cap_breakdown. Depth charts: v_depth_charts (cross-era view — preferred), depth_charts, depth_charts_2025. Play-by-play: play_by_play, pbp_participation, ftn_charting.",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_player_info",
        "description": "Get detailed player biography and cross-platform IDs for a specific player. Requires player_gsis_id (use search_players first to find it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "player_gsis_id": {
                    "type": "string",
                    "description": "Player's GSIS ID (e.g. '00-0033873' for Patrick Mahomes)",
                },
            },
            "required": ["player_gsis_id"],
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
        "name": "set_table",
        "description": (
            "Replace the live table in a Table View chat with the rows produced by this SQL query. "
            "Only available in 'Change table' turns of a table-view chat — never in regular chat. "
            "The user has selected a row cap via the table-size dropdown; the cap is enforced server-side, "
            "so write LIMIT clauses up to that cap and don't try to exceed it. The rows do NOT come back "
            "in the tool result — only a brief summary (row_count, columns) — so build the SQL to be "
            "self-contained and don't expect to inspect the cells. Standard sandbox rules: SELECT/WITH only, "
            "single statement, read-only. Call this exactly once per 'Change table' turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement. Read-only. Result becomes the new table.",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "create_report",
        "description": (
            "Create a new Report (a table-view chat) populated with the rows from this SQL query, "
            "then auto-navigate the user to it. Use this whenever the user wants to *view, browse, "
            "sort, or iterate on* tabular data — anything beyond a one-shot answer. The user lands "
            "in the Reports tab with the table already filled in and can chat with a fresh agent "
            "there to refine columns, download as CSV, or run follow-ups. "
            "Pick a short descriptive `title` (3-8 words, e.g. 'Top 25 PPR Scorers 2024'). "
            "Rows are capped at 500 server-side. "
            "Prefer this over inline markdown tables when there are >~10 rows or >~5 columns. "
            "Distinct from `create_csv_export`: that tool produces a download link and is only for "
            "explicit 'download/save as CSV' requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL SELECT or WITH statement. Must be read-only.",
                },
                "title": {
                    "type": "string",
                    "description": "Short descriptive title for the Report (3-8 words).",
                },
            },
            "required": ["sql", "title"],
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
    {
        "name": "run_in_editor",
        "description": (
            "Place this SQL into the user's Database browser editor and run it. "
            "Use this when the user wants to SEE the results in their main editor view "
            "(asks to 'run', 'execute', 'do', 'show me', 'pull up' a query). "
            "DO NOT use this when the user just wants the SQL text for themselves "
            "('give me the SQL', 'just write the query', 'how would I write…') — "
            "in those cases respond inline with a fenced ```sql block. "
            "DO NOT use this to research a query yourself — that's `execute_sql`. "
            "After you call this, the user sees the rows directly in their editor; "
            "your follow-up message should NOT re-show the SQL or the rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The read-only SELECT/WITH statement to run in the user's editor.",
                },
            },
            "required": ["sql"],
        },
    },
]

# Typed tool definitions — preferred import for consumers
TOOLS: list[ToolDefinition] = [ToolDefinition.from_dict(d) for d in TOOL_DEFINITIONS]
