# Player Profile Guide

Player biography, IDs, draft history, combine measurables, depth chart.

## `players` — roster & bio (24K rows, 1999–2025)

- **ID: `gsis_id`** (format: `00-0033873`). This is the primary key across the database.
- Key columns: `display_name`, `position`, `latest_team` (NOT `current_team`), `college_name` (NOT `college`), `height`, `weight`, `draft_year`, `draft_round`, `draft_pick`, `draft_team`, `birth_date`, `rookie_year`, `headshot_url`.
- **Use `search_players` (tool)** to find a player by name and resolve `gsis_id` before any subsequent query. For 3+ known names or unambiguous cases, skip the tool and query directly with `WHERE display_name IN (...)`.

## `player_ids` — cross-platform ID bridge (7.7K rows)

Maps a player's GSIS ID to their IDs on other platforms. Only used as a **JOIN bridge** for supplementary tables that don't expose GSIS IDs.

- `gsis_id` → joins to `players.gsis_id`, and to `game_stats.player_id` / `season_stats.player_id` (despite the name mismatch, those hold GSIS IDs).
- `pfr_id` (e.g. `MahoPa00`) → bridges to `snap_counts.pfr_player_id` and `pfr_advanced.pfr_id`.
- `espn_id` → bridges to `qbr.player_id` (requires `CAST` for type match).

```sql
-- snap_counts → players
JOIN player_ids pi ON pi.pfr_id = sc.pfr_player_id
JOIN players p ON p.gsis_id = pi.gsis_id

-- pfr_advanced → players
JOIN player_ids pi ON pi.pfr_id = pa.pfr_id
JOIN players p ON p.gsis_id = pi.gsis_id

-- qbr → players
JOIN player_ids pi ON CAST(pi.espn_id AS INTEGER) = CAST(q.player_id AS INTEGER)
JOIN players p ON p.gsis_id = pi.gsis_id
```

**Key naming gotcha**: `snap_counts.pfr_player_id` vs `player_ids.pfr_id` — DIFFERENT column names on each side of the bridge. Don't try `player_ids.pfr_player_id` (doesn't exist).

## `draft_picks` — NFL draft history (12.7K rows, 1980–2025)

- Has `gsis_id` column — direct join to `players` (no bridge needed).
- Columns: `season`, `round`, `pick`, `team` (drafting team — NOT `drafting_team`), `pfr_player_name` (NOT `player_name`), `position`, `college` (here it IS `college`, not `college_name`), plus career aggregates `w_av`, `car_av`, `probowls`, `allpro`.
- **Always `get_schema('draft_picks')` before first query** — aggregate columns are non-obvious.

## `combine` — NFL combine measurables (8.6K rows, 2000–2025)

- **NO join edges in the schema.** `combine` does NOT have `gsis_id`, `pfr_id`, or any other FK. Query it standalone by `player_name` (matches `pfr_player_name` in draft_picks closest) and `pos` (NOT `position`).
- Key columns: `season`, `player_name`, `pos`, `school` (NOT `college`), `ht`, `wt`, `forty`, `bench`, `vertical`, `broad_jump`, `cone`, `shuttle`, `draft_year`, `draft_round`, `draft_pick`, `draft_team`.
- **Always `get_schema('combine')` before first query.**

## `depth_charts` (2001–2024) + `depth_charts_2025`

Two tables — check which season range you need.

### `depth_charts` (869K rows, 2001–2024)

- **ID: `gsis_id`** — direct join to players.
- Columns: `season`, `week`, `club_code`, `position`, `depth_team` (string `'1'`, `'2'`, `'3'`), `full_name`, `game_type`.
- `depth_team = '1'` → starter.
- `game_type` values: `'REG'`, `'WC'`, `'DIV'`, `'CON'`, `'SB'` (NOT `'POST'`).

### `depth_charts_2025` (477K rows, 2025 only)

- **ID: `gsis_id`** — direct join.
- **Different schema**: uses `dt` (DATETIME) instead of `season` / `week`. Same other columns (`club_code`, `position`, `depth_team`, `full_name`).
- Use this table for 2025 depth charts; use `depth_charts` for historical (2001–2024).

## Templates

**Player bio by name**
```sql
SELECT display_name, position, latest_team, college_name, draft_year, draft_round, draft_pick
FROM players WHERE display_name = 'Patrick Mahomes';
```

**Draft history for one school**
```sql
SELECT dp.season, dp.round, dp.pick, dp.team, dp.pfr_player_name, dp.position, dp.w_av
FROM draft_picks dp
WHERE dp.college = 'Alabama' AND dp.season BETWEEN 2015 AND 2024
ORDER BY dp.season DESC, dp.pick;
```

**Combine measurables for a position (one draft class)**
```sql
SELECT player_name, pos, school, ht, wt, forty, vertical, broad_jump, cone, shuttle
FROM combine
WHERE season = 2024 AND pos = 'WR' AND forty IS NOT NULL
ORDER BY forty ASC LIMIT 20;
```

**Current starters (2024) at a position**
```sql
SELECT DISTINCT d.club_code, p.display_name
FROM depth_charts d JOIN players p ON p.gsis_id = d.gsis_id
WHERE d.season = 2024 AND d.week = 1 AND d.game_type = 'REG'
  AND d.position = 'QB' AND d.depth_team = '1'
ORDER BY d.club_code;
```

**Cross-platform ID lookup for one player**
```sql
SELECT p.display_name, pi.gsis_id, pi.pfr_id, pi.espn_id, pi.yahoo_id
FROM player_ids pi JOIN players p ON p.gsis_id = pi.gsis_id
WHERE p.display_name = 'Patrick Mahomes';
```

## Gotchas

- `players.latest_team` (NOT `current_team`), `players.college_name` (NOT `college`).
- `draft_picks.college` (NOT `college_name`), `draft_picks.pfr_player_name` (NOT `player_name`).
- `combine.pos` (NOT `position`), `combine.school` (NOT `college`). No join edges.
- `depth_charts.game_type` = `'REG'/'WC'/'DIV'/'CON'/'SB'` — no `'POST'` value.
- `depth_charts_2025` uses `dt` datetime column; `depth_charts` uses `season`/`week`.
- 2025 signings / roster changes flow into `players.latest_team` over time — latest_team is the most current team, not a snapshot of any given season.
