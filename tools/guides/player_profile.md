# Player Profile Guide

Player biography, IDs, draft history, combine measurables, depth chart.

## `players` — roster & bio (24K rows, 1999–2025)

- **IDs: `player_gsis_id`** (format: `00-0033873`, primary key across the database), plus `player_pfr_id` and `player_espn_id` for direct joins to the supplementary tables that use those ID systems.
- Key columns: `display_name`, `position`, `latest_team` (NOT `current_team`), `college_name` (NOT `college`), `height`, `weight`, `draft_year`, `draft_round`, `draft_pick`, `draft_team`, `birth_date`, `rookie_year`, `headshot_url`.
- **Use `search_players` (tool)** to find a player by name and resolve `player_gsis_id` before any subsequent query. For 3+ known names or unambiguous cases, skip the tool and query directly with `WHERE display_name IN (...)`.

## `player_ids` — cross-platform ID bridge (7.7K rows)

Since the 2026-04-23 ID normalization, **you rarely need this table** — `players` carries `player_gsis_id` / `player_pfr_id` / `player_espn_id` directly, and every supplementary table joins to `players` via one of those columns. `player_ids` is only required when the caller is starting from a non-canonical ID (yahoo_id, sleeper_id, fantasy_id, pff_id, espn_name, etc.) and needs to resolve back to GSIS.

Columns: `gsis_id`, `pfr_id`, `espn_id`, `yahoo_id`, `sleeper_id`, `fantasy_data_id`, `pff_id`, and many more platform-specific IDs. (Note the short names — this table uses `gsis_id` / `pfr_id` / `espn_id`, not the `player_*_id` convention, because each row IS the ID mapping.)

```sql
-- Example: resolve a sleeper_id to the player's full bio.
SELECT p.* FROM players p
JOIN player_ids pi ON pi.gsis_id = p.player_gsis_id
WHERE pi.sleeper_id = '4046';
```

## `draft_picks` — NFL draft history (12.7K rows, 1980–2025)

- Has `player_gsis_id` + `player_pfr_id` — direct join to `players` on either.
- Columns: `season`, `round`, `pick`, `team` (drafting team — NOT `drafting_team`), `pfr_player_name` (NOT `player_name`), `position`, `college` (here it IS `college`, not `college_name`), plus career aggregates `w_av`, `car_av`, `probowls`, `allpro`.
- **Always `get_schema('draft_picks')` before first query** — aggregate columns are non-obvious.

## `combine` — NFL combine measurables (8.6K rows, 2000–2025)

- **ID: `player_pfr_id`** — direct join to `players.player_pfr_id`. (Previously combine had no join edges; post-rename it does.)
- Key columns: `season`, `player_name`, `pos` (NOT `position`), `school` (NOT `college`), `ht`, `wt`, `forty`, `bench`, `vertical`, `broad_jump`, `cone`, `shuttle`, `draft_year`, `draft_round`, `draft_pick`, `draft_team`.
- Not every combine row has a `player_pfr_id` populated — some undrafted participants don't get a PFR profile. For those, fall back to the name+draft_year match below.
- **Always `get_schema('combine')` before first query.**

## `depth_charts` (2001–2024) + `depth_charts_2025`

Two tables — check which season range you need.

### `depth_charts` (869K rows, 2001–2024)

- **ID: `player_gsis_id`** — direct join to players.
- Columns: `season`, `week`, `club_code`, `position`, `depth_team` (string `'1'`, `'2'`, `'3'`), `full_name`, `game_type`.
- `depth_team = '1'` → starter.
- `game_type` values: `'REG'`, `'WC'`, `'DIV'`, `'CON'`, `'SB'` (NOT `'POST'`).

### `depth_charts_2025` (477K rows, 2025 only)

- **ID: `player_gsis_id`** — direct join. Also carries `player_espn_id`.
- **Completely different schema from `depth_charts`.** Actual columns:
  `dt` (TEXT, ISO datetime `'2026-02-13T...'`), `team` (NOT `club_code`), `player_name`, `player_gsis_id`, `player_espn_id`, `pos_grp_id`, `pos_grp`, `pos_id`, `pos_name`, `pos_abb`, `pos_slot`, `pos_rank` (1 = starter).
- **No `season` / `week` / `game_type` / `depth_team` columns.** Use `dt` for time filtering and `pos_rank = 1` for starters.

```sql
-- current starting QBs (latest snapshot per team)
WITH latest AS (
  SELECT team, MAX(dt) AS most_recent
  FROM depth_charts_2025
  GROUP BY team
)
SELECT d.team, d.player_name, d.pos_abb, d.pos_rank, d.dt
FROM depth_charts_2025 d
JOIN latest l ON l.team = d.team AND l.most_recent = d.dt
WHERE d.pos_abb = 'QB' AND d.pos_rank = 1
ORDER BY d.team;
```

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

**Combine → players cross-reference (preferred: player_pfr_id direct join)**
```sql
SELECT c.player_name, c.pos, c.school, c.forty, c.vertical, c.broad_jump,
       p.player_gsis_id, p.display_name, p.latest_team
FROM combine c
JOIN players p ON p.player_pfr_id = c.player_pfr_id
WHERE c.player_name LIKE '%Mahomes%'
  AND c.pos = 'QB';
```
Fallback when `player_pfr_id` is NULL on a combine row (undrafted prospects often have no PFR profile), match on `display_name` + `draft_year` + `pos`:
```sql
LEFT JOIN players p
  ON p.display_name = c.player_name
 AND p.draft_year   = c.draft_year
```
Watch for suffix drift (`"Patrick Mahomes II"` vs `"Patrick Mahomes"`), nickname differences, and apostrophes.

**Current starters (2024) at a position**
```sql
SELECT DISTINCT d.club_code, p.display_name
FROM depth_charts d JOIN players p ON p.player_gsis_id = d.player_gsis_id
WHERE d.season = 2024 AND d.week = 1 AND d.game_type = 'REG'
  AND d.position = 'QB' AND d.depth_team = '1'
ORDER BY d.club_code;
```

**Cross-platform ID lookup for one player**
```sql
SELECT p.display_name, p.player_gsis_id, p.player_pfr_id, p.player_espn_id,
       pi.yahoo_id, pi.sleeper_id
FROM players p
LEFT JOIN player_ids pi ON pi.gsis_id = p.player_gsis_id
WHERE p.display_name = 'Patrick Mahomes';
```

## Gotchas

- `players.latest_team` (NOT `current_team`), `players.college_name` (NOT `college`).
- `draft_picks.college` (NOT `college_name`), `draft_picks.pfr_player_name` (NOT `player_name`).
- `combine.pos` (NOT `position`), `combine.school` (NOT `college`).
- `depth_charts.game_type` = `'REG'/'WC'/'DIV'/'CON'/'SB'` — no `'POST'` value.
- `depth_charts_2025` uses `dt` datetime column; `depth_charts` uses `season`/`week`.
- 2025 signings / roster changes flow into `players.latest_team` over time — latest_team is the most current team, not a snapshot of any given season.
- `player_ids` uses short names (`gsis_id`, `pfr_id`, `espn_id`) rather than the `player_*_id` convention used on the other tables, because each row represents an identity mapping rather than a reference to a player.
