---
name: nfl-stats
description: Look up NFL player stats, game data, fantasy points, and advanced analytics from the local nflverse API. Use when the user asks any question about NFL players, stats, fantasy football, or game data.
allowed-tools: Bash(sqlite3 *, curl *)
---

# NFL Stats Lookup

Answer NFL stats questions by querying the local SQLite databases directly.

- **Main DB**: `NFLVERSE/data/nflverse_v2.db` (13 tables, ~200 MB)
- **PBP DB**: `NFLVERSE/data/pbp_v2.db` (1 table: `play_by_play`, ~550 MB)

For full column reference, read [docs/API.md](../../../docs/API.md).

## How to Answer a Question

1. **Identify the right table** using this mapping:

| Question type | Table | Join needed? |
|---|---|---|
| Player bio / headshot / team | `players` | No |
| Weekly game log | `game_stats` | + `players` |
| Season totals / fantasy leaderboard | `season_stats` | + `players` |
| Snap counts / usage | `snap_counts` | + `player_ids` + `players` (PFR bridge) |
| Next Gen Stats (CPOE, separation, RYOE) | `ngs_stats` | No (has player name) |
| PFR advanced (pressure, drops, YBC) | `pfr_advanced` | + `player_ids` + `players` (PFR bridge) |
| ESPN QBR | `qbr` | + `player_ids` + `players` (ESPN bridge) |
| Depth chart / starters | `depth_charts` | No |
| Draft history | `draft_picks` | No |
| Combine results | `combine` | No |
| Scores / schedule / weather | `games` | No |
| Play-level EPA / WPA / CPOE | `play_by_play` | No (has player names) |
| Cross-platform IDs | `player_ids` | No |

2. **Write a SQL query** with the appropriate joins, filters, and aggregations.

3. **Execute with `sqlite3`.**

## Querying with sqlite3

```bash
# Main database
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "SELECT ..."

# Play-by-play (separate database)
sqlite3 -header -separator '  |  ' NFLVERSE/data/pbp_v2.db "SELECT ..."
```

Always use `-header -separator '  |  '` for readable output.

## Join Reference

### Direct joins (GSIS ID)

Tables using GSIS ID join directly to `players`:

```sql
-- game_stats → players
game_stats.player_id = players.gsis_id

-- season_stats → players
season_stats.player_id = players.gsis_id

-- ngs_stats → players
ngs_stats.player_gsis_id = players.gsis_id

-- depth_charts → players
depth_charts.gsis_id = players.gsis_id

-- depth_charts_2025 → players
depth_charts_2025.gsis_id = players.gsis_id

-- draft_picks → players
draft_picks.gsis_id = players.gsis_id
```

### Bridge joins via player_ids (PFR ID)

`snap_counts` and `pfr_advanced` use PFR IDs — bridge through `player_ids`:

```sql
-- snap_counts → player_ids → players
snap_counts
  LEFT JOIN player_ids ON snap_counts.pfr_player_id = player_ids.pfr_id
  LEFT JOIN players ON player_ids.gsis_id = players.gsis_id

-- pfr_advanced → player_ids → players
pfr_advanced
  LEFT JOIN player_ids ON pfr_advanced.pfr_id = player_ids.pfr_id
  LEFT JOIN players ON player_ids.gsis_id = players.gsis_id
```

### Bridge joins via player_ids (ESPN ID)

`qbr` uses ESPN IDs — bridge through `player_ids` with CAST:

```sql
-- qbr → player_ids → players
qbr
  LEFT JOIN player_ids ON player_ids.espn_id = CAST(qbr.player_id AS REAL)
  LEFT JOIN players ON player_ids.gsis_id = players.gsis_id
```

## Common Query Patterns

**Leaderboard with names (join + aggregate):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, SUM(game_stats.passing_yards) AS total_yards
FROM game_stats
LEFT JOIN players ON game_stats.player_id = players.gsis_id
WHERE game_stats.season = 2024 AND game_stats.season_type = 'REG'
GROUP BY players.display_name
ORDER BY total_yards DESC
LIMIT 10;
"
```

**Player game log:**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, game_stats.week, game_stats.opponent_team,
       game_stats.completions, game_stats.attempts, game_stats.passing_yards,
       game_stats.passing_tds, game_stats.passing_interceptions, game_stats.fantasy_points_ppr
FROM game_stats
LEFT JOIN players ON game_stats.player_id = players.gsis_id
WHERE players.display_name = 'Patrick Mahomes' AND game_stats.season = 2024
ORDER BY game_stats.week ASC;
"
```

**Snap counts with player names (PFR bridge join):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, snap_counts.position, snap_counts.team,
       snap_counts.offense_snaps, snap_counts.offense_pct
FROM snap_counts
LEFT JOIN player_ids ON snap_counts.pfr_player_id = player_ids.pfr_id
LEFT JOIN players ON player_ids.gsis_id = players.gsis_id
WHERE snap_counts.season = 2024 AND snap_counts.week = 1
ORDER BY snap_counts.offense_snaps DESC
LIMIT 20;
"
```

**QBR with player names (ESPN bridge join):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, qbr.qbr_total, qbr.pts_added, qbr.qb_plays, qbr.epa_total
FROM qbr
LEFT JOIN player_ids ON player_ids.espn_id = CAST(qbr.player_id AS REAL)
LEFT JOIN players ON player_ids.gsis_id = players.gsis_id
WHERE qbr.season = 2023
  AND qbr.week_text = 'Season Total'
  AND qbr.season_type = 'Regular'
ORDER BY qbr.qbr_total DESC
LIMIT 10;
"
```

**PBP aggregation (EPA per play by team):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/pbp_v2.db "
SELECT posteam, AVG(epa) AS avg_epa, COUNT(play_id) AS plays
FROM play_by_play
WHERE season = 2024 AND qb_dropback = 1
GROUP BY posteam
ORDER BY avg_epa DESC;
"
```

**Search player by name:**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT gsis_id, display_name, position, latest_team
FROM players
WHERE display_name LIKE '%Jefferson%'
LIMIT 10;
"
```

**Fantasy leaderboard (season_stats):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, players.position, players.latest_team,
       season_stats.games, season_stats.receptions, season_stats.receiving_yards,
       season_stats.receiving_tds, season_stats.fantasy_points_ppr
FROM season_stats
LEFT JOIN players ON season_stats.player_id = players.gsis_id
WHERE season_stats.season = 2024 AND season_stats.season_type = 'REG'
  AND players.position = 'WR'
ORDER BY season_stats.fantasy_points_ppr DESC
LIMIT 20;
"
```

**PFR advanced stats (bridge join):**
```bash
sqlite3 -header -separator '  |  ' NFLVERSE/data/nflverse_v2.db "
SELECT players.display_name, pfr_advanced.pass_attempts,
       pfr_advanced.times_pressured, pfr_advanced.pressure_pct,
       pfr_advanced.drops, pfr_advanced.drop_pct, pfr_advanced.pocket_time
FROM pfr_advanced
LEFT JOIN player_ids ON pfr_advanced.pfr_id = player_ids.pfr_id
LEFT JOIN players ON player_ids.gsis_id = players.gsis_id
WHERE pfr_advanced.season = 2024 AND pfr_advanced.stat_type = 'pass'
ORDER BY pfr_advanced.pass_attempts DESC
LIMIT 20;
"
```

## Schema Discovery

Use `curl` to discover table schemas from the running API:

```bash
curl -s "http://localhost:8001/schema"
curl -s "http://localhost:8001/schema/game_stats"
```

Or query SQLite directly:

```bash
sqlite3 NFLVERSE/data/nflverse_v2.db ".schema players"
sqlite3 NFLVERSE/data/nflverse_v2.db "PRAGMA table_info(game_stats);"
```

## Critical Gotchas

- **Ambiguous columns in joins**: Always qualify with `table.column` for `season`, `week`, `team`, `position`, `gsis_id`
- **NGS stat_type**: `passing` / `rushing` / `receiving`; `week=0` = season totals
- **PFR stat_type**: `pass` / `rush` / `rec` (different naming from NGS!)
- **QBR**: `game_week` is an INTEGER; `week_text` is TEXT (`"Season Total"`); `season_type` is `"Regular"` / `"Postseason"`
- **PBP**: Always filter by `season` to avoid timeouts (1.28M rows). Query from `pbp_v2.db`, not `nflverse_v2.db`
- **combine**: No join edges to other tables — query separately
- **Bridge joins**: `snap_counts` and `pfr_advanced` need `player_ids` bridge for player names; `qbr` also needs bridge with CAST
- **Team abbreviations**: Always uppercase (KC, SF, BUF, etc.)
- **Reserved words**: Quote with double quotes in SQL: `"pass"`, `"int"`, `"drop"`, `"rank"`, `"run"`, `"sack"`, `"penalty"`, `"order"`, `"group"`
- **Data years**: Most tables through 2025. QBR only through 2023. Snap counts from 2015+, NGS from 2016+, PFR from 2018+
- **depth_charts_2025**: Uses `dt` (date) instead of `season`/`week`, and `pos_rank` instead of `depth_team`

## Workflow

1. Identify the right table(s) and joins from the mapping above
2. Write SQL with proper joins, filters, GROUP BY, and ORDER BY
3. Execute with `sqlite3` — use the main DB for most tables, `pbp_v2.db` for play-by-play
4. If you're unsure about column names, check with `curl -s "http://localhost:8001/schema/{table}"` or `PRAGMA table_info({table})`
5. Always present results in a clean, readable format (tables, bullet points)
