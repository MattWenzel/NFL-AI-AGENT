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

## Depth charts

Three tables/views. **Default to `v_depth_charts`** — it's a DuckDB view that UNIONs the two base tables with a normalized 12-column schema, so cross-era queries just work. The base tables are still there for era-specific columns.

### `v_depth_charts` (1.35M rows, 2001–2025) — preferred

Normalized UNION of `depth_charts` + `depth_charts_daily`. Columns:

`season` (BIGINT), `week` (INTEGER, NULL for preseason), `dt` (VARCHAR, NULL on legacy / ISO 8601 on 2025), `team`, `player_gsis_id`, `player_espn_id` (NULL on legacy), `position` (general: QB/WR/CB/DE/OLB/T/G/K/P/FS/SS/…), `pos_abb` (slot-specific: RCB/LDE/WLB/LT/LG/…), `depth_rank` (1–3 legacy, 1–15 on 2025), `formation` (Offense/Defense/Special Teams), `pos_grp` (2025 only), `source` (`'legacy'` / `'v2025'`).

**Starter filter:** `depth_rank = 1`. **Cross-era lookups:** filter on `position` (not `pos_abb`) — it's the general value that's populated on both sides.

```sql
-- Starting QB for KC in Week 17 across eras
SELECT v.source, v.season, v.week, p.display_name
FROM v_depth_charts v
JOIN players p ON p.player_gsis_id = v.player_gsis_id
WHERE v.team = 'KC' AND v.depth_rank = 1 AND v.position = 'QB'
  AND v.season BETWEEN 2023 AND 2025
  AND v.week = 17
ORDER BY v.season, v.source;
```

**Daily grain caveat (2025+):** on the v2025 side, a single season-team-position can have multiple `depth_rank = 1` rows because 2025+ is daily-snapshot grain (starter can change across `dt` values). For "primary starter," aggregate with `argmax(player_gsis_id, dt)` per (team, season, position) or filter by a specific `dt`. For any point-in-time / deep-rank (≥4) query, drop to `depth_charts_daily` directly.

### `depth_charts` (869K rows, 2001–2024) — legacy base table

Use directly when you need `game_type` ('REG'/'WC'/'DIV'/'CON'/'SB' — NOT in the view) for playoff filtering, or legacy columns `elias_id`, `first_name`, `last_name`. ID: `player_gsis_id`. Depth string is `depth_team` = `'1'`/`'2'`/`'3'`; rank column `depth_rank` is the same value but integer.

### `depth_charts_daily` (787K rows, 2025+, one season per upstream file; has a `season` column) — daily base table

Use directly for:
- Point-in-time queries (filter by `dt` directly)
- Deep-rank queries (`pos_rank >= 4` — only 2025+ tracks these)
- Detailed 2025 columns not in the view: `pos_grp_id`, `pos_id`, `pos_name`, `pos_slot`, `player_name`

```sql
-- current starting QBs (latest snapshot per team, 2025-specific)
WITH latest AS (
  SELECT team, MAX(dt) AS most_recent
  FROM depth_charts_daily
  GROUP BY team
)
SELECT d.team, d.player_name, d.pos_abb, d.pos_rank, d.dt
FROM depth_charts_daily d
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

**Current starters at a position, any season (uses the view)**
```sql
SELECT DISTINCT v.team, p.display_name
FROM v_depth_charts v
JOIN players p ON p.player_gsis_id = v.player_gsis_id
WHERE v.season = 2024 AND v.week = 1
  AND v.position = 'QB' AND v.depth_rank = 1
ORDER BY v.team;
```
For pre-2025 playoff starters, drop to `depth_charts` directly and filter on `game_type IN ('WC','DIV','CON','SB')` — the view doesn't carry `game_type`.

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
- **Default to `v_depth_charts`** for depth-chart queries. It UNIONs the two base tables with a normalized 12-col schema and a `source` provenance tag.
- The view has no `game_type` column — for playoff-round filtering (`game_type IN ('WC','DIV','CON','SB')`) drop to `depth_charts` (legacy) directly. `depth_charts.game_type` never takes `'POST'`.
- `v_depth_charts.depth_rank` is 1–3 on legacy and 1–15 on 2025+. `WHERE depth_rank >= 4` returning only 2025+ rows is correct — pre-2025 nflverse didn't track it.
- 2025+ is daily grain — multiple `depth_rank = 1` rows per (team, season, position) can exist as the starter changed across snapshots. Aggregate with `argmax(player_gsis_id, dt)` or filter by a specific `dt` in `depth_charts_daily`.
- 2025 signings / roster changes flow into `players.latest_team` over time — latest_team is the most current team, not a snapshot of any given season.
- `player_ids` uses short names (`gsis_id`, `pfr_id`, `espn_id`) rather than the `player_*_id` convention used on the other tables, because each row represents an identity mapping rather than a reference to a player.
