# Play-by-Play Guide

The `play_by_play` table has 1,279,628 rows × 372 columns (1999–2025). **Always include at least one filter** (season, week, team, or player) — unfiltered scans time out.

Filter by `season`, `week`, or team before aggregating.

## READ THIS BEFORE ANY DRIVE-LEVEL QUERY

**`drive_*` columns (`drive_time_of_possession`, `drive_play_count`, `fixed_drive_result`, `drive_yards_penalized`, `drive_first_downs`, etc.) REPEAT on every play of the same drive.** A 20-play drive produces 20 identical rows with the same drive stats. If you `SELECT` drive columns without collapsing to one-row-per-drive, your output is ~14× duplicated on average (that's the league avg plays/drive).

**The fix is a filter, not `DISTINCT`.** Use one of these in the WHERE clause:
- `drive_play_id_started = play_id` — the drive's first play (recommended).
- `drive_play_id_ended = play_id` — the drive's last play.

`DISTINCT` on the full SELECT tuple appears to work but silently collapses different drives that happened to share the same time-of-possession + play_count + result, which is common enough (e.g. two 3-play field-goal drives at 1:30). Use the filter.

For deeper drive analytics (TOP parsing, three-and-out rates, scoring-drive rates, longest-drive leaderboards), load `get_guide({"topic": "drives"})` — there's a dedicated guide.

## `play_by_play` has NO `game_type` column

Filtering `WHERE game_type = 'SB'` on PBP errors out. `play_by_play` uses **`season_type`** (`'REG'` / `'POST'` — binary) plus `week` for postseason round granularity. **Week numbers for playoff rounds shift by era:**

- 1999–2020 (16-game reg season): WC=18, DIV=19, CONF=20, SB=21
- 2021–present (17-game reg season): WC=19, DIV=20, CONF=21, SB=22

**The safer approach is to join to `games`** and filter on `game_type`:
```sql
FROM play_by_play pbp JOIN games g ON g.game_id = pbp.game_id
WHERE g.game_type = 'SB'   -- or 'WC' / 'DIV' / 'CON'
```
See `get_guide({"topic": "postseason"})` for the full playoff-encoding cheatsheet across every table.

## Topic → columns

| Question type | Columns you need |
|---|---|
| Clutch / 2-min / late-game | `game_seconds_remaining`, `score_differential`, `wp`, `wpa`, `qtr` |
| Red zone / goal line | `yardline_100` (≤20 / ≤5), `touchdown`, `first_down` |
| QB passing | `passer_player_id`, `complete_pass`, `pass_touchdown`, `air_yards`, `cpoe`, `epa` |
| Rushing | `rusher_player_id`, `rush_attempt`, `rushing_yards`, `rush_touchdown`, `yards_after_contact` |
| Receiving / air yards / YAC | `receiver_player_id`, `air_yards`, `yards_after_catch`, `complete_pass` |
| Defense: sacks | `sack=1`, `sack_player_id/name`, `half_sack_1/2_player_id/name` |
| Defense: INTs | `interception=1`, `interception_player_id/name` |
| Defense: tackles | `solo_tackle_1/2_player_id`, `assist_tackle_1–4_player_id`, `tackle_for_loss_1/2_player_id` |
| Fumbles / recoveries | `fumble`, `fumble_lost`, `fumbled_1/2_player_id`, `fumble_recovery_1/2_player_id`, `forced_fumble_player_1/2_player_id` |
| Field goals / punts / returns | `field_goal_attempt`, `field_goal_result`, `kicker_player_id`, `punter_player_id`, `punt_returner_player_id`, `kickoff_returner_player_id`, `kick_distance` |
| Penalties | `penalty=1`, `penalty_player_id/name`, `penalty_team`, `penalty_yards`, `penalty_type` |
| 3rd / 4th down | `down`, `ydstogo`, `third_down_converted`, `fourth_down_converted` |
| Drive outcomes | `fixed_drive`, `fixed_drive_result`, `drive_play_count`, `drive_time_of_possession`, `drive_yards_penalized` |
| EPA / WPA | `epa`, `wpa`, `wp`, `vegas_wpa`, `cp`, `cpoe`, `xpass`, `pass_oe`, `qb_epa` |
| Game context | `home_team`, `away_team`, `posteam`, `defteam`, `home_coach`, `away_coach`, `roof`, `surface`, `temp`, `wind` |

Every `*_player_id` column uses GSIS ID format (`00-0035228`) and joins to `players.player_gsis_id`.

## Key column cheat sheet

- **`score_differential`** = `posteam_score - defteam_score`. **Negative = losing**. "Down by 1 score": `BETWEEN -8 AND -1`. "Down by 2+": `<= -9`.
- **`yardline_100`** = yards to opponent's end zone. Lower = closer to scoring. Red zone: `<= 20`. Goal line: `<= 5`.
- **`game_seconds_remaining`**: 0 at final whistle, ~3600 at kickoff. "Last 2 min": `<= 120`. "Last 5 min": `<= 300`.
- **`qtr`**: 1, 2, 3, 4, 5 (5 = OT). `game_half`: `Half1` / `Half2` / `Overtime`.
- **`down`**: 1–4 (NULL on kickoffs / non-scrimmage plays).
- **`play_type`**: `pass`, **`run`** (NOT "rush"), `punt`, `field_goal`, `kickoff`, `extra_point`, `qb_kneel`, `qb_spike`, `no_play`.
- **`pass = 1`** includes sacks and scrambles. **`pass_attempt = 1`** excludes sacks.
- **`cpoe` is 0–100 scale**, NOT 0–1 (unlike `cp` which is 0–1).
- **`qb_epa` ≠ `epa`** — `qb_epa` is QB-credited EPA on pass/sack plays; `epa` is play-level.
- **Player-name columns abbreviate** (`P.Mahomes`). Join to `players` via the `*_player_id` column for full names.

## Templates (copy verbatim, adjust filters)

**Clutch WPA swings (second half, competitive game)**
```sql
SELECT season, week, posteam, defteam, qtr, down, ydstogo, yardline_100,
       play_type, desc, wpa, wp, yards_gained, touchdown
FROM play_by_play
WHERE season = 2024 AND wpa IS NOT NULL
  AND play_type IN ('pass','run')
  AND qtr >= 3 AND wp BETWEEN 0.05 AND 0.95
ORDER BY wpa DESC LIMIT 25;
```

**Sack leaders (no defensive stats table — use PBP)**
```sql
SELECT p.display_name, COUNT(*) AS sacks
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.sack_player_id
WHERE pbp.season = 2024 AND pbp.sack = 1 AND pbp.sack_player_id IS NOT NULL
GROUP BY p.player_gsis_id, p.display_name ORDER BY sacks DESC LIMIT 20;
```
For half-sacks, union `half_sack_1_player_id` and `half_sack_2_player_id` into the same aggregation.

**Interception leaders**
```sql
SELECT p.display_name, COUNT(*) AS ints
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.interception_player_id
WHERE pbp.season = 2024 AND pbp.interception = 1
GROUP BY p.player_gsis_id, p.display_name ORDER BY ints DESC LIMIT 20;
```

**Red-zone passing by QB**
```sql
SELECT p.display_name,
       COUNT(*) AS att, SUM(complete_pass) AS comp, SUM(pass_touchdown) AS tds,
       ROUND(AVG(epa), 3) AS epa_per_play, ROUND(AVG(cpoe), 1) AS cpoe
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.passer_player_id
WHERE pbp.season = 2024 AND pbp.yardline_100 <= 20 AND pbp.pass_attempt = 1
GROUP BY p.player_gsis_id, p.display_name HAVING COUNT(*) >= 30
ORDER BY tds DESC LIMIT 20;
```

**Third-down conversion rate by team**
```sql
SELECT posteam,
       SUM(third_down_converted) AS conv,
       SUM(third_down_failed) AS fail,
       ROUND(100.0 * SUM(third_down_converted)
             / NULLIF(SUM(third_down_converted) + SUM(third_down_failed), 0), 1) AS pct
FROM play_by_play
WHERE season = 2024 AND down = 3 AND play_type IN ('pass','run')
GROUP BY posteam ORDER BY pct DESC;
```

**Deep-ball accuracy (20+ air yards)**
```sql
SELECT p.display_name, COUNT(*) AS att,
       ROUND(100.0 * AVG(complete_pass), 1) AS pct,
       ROUND(AVG(cpoe), 1) AS cpoe
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.passer_player_id
WHERE pbp.season = 2024 AND pbp.air_yards >= 20 AND pbp.pass_attempt = 1
GROUP BY p.player_gsis_id, p.display_name HAVING COUNT(*) >= 20
ORDER BY cpoe DESC LIMIT 20;
```

**Drive outcomes** — one row per drive (note the mandatory `drive_play_id_started = play_id` filter)
```sql
SELECT posteam, fixed_drive_result, COUNT(*) AS drives,
       ROUND(AVG(drive_play_count), 1) AS avg_plays
FROM play_by_play
WHERE season = 2024 AND drive_play_id_started = play_id
GROUP BY posteam, fixed_drive_result ORDER BY posteam, drives DESC;
```

For longest-drive leaderboards, three-and-out rates, scoring-drive rates, and the canonical MM:SS → seconds parser, load `get_guide({"topic": "drives"})`.

**Penalty leaders (season)**
```sql
SELECT p.display_name, pbp.penalty_team AS team,
       COUNT(*) AS flags,
       SUM(pbp.penalty_yards) AS yards
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.penalty_player_id
WHERE pbp.season = 2024 AND pbp.penalty = 1 AND pbp.penalty_player_id IS NOT NULL
GROUP BY p.player_gsis_id, p.display_name, pbp.penalty_team
ORDER BY flags DESC LIMIT 20;
```
Filter by `pbp.penalty_type` (e.g. `'Holding'`, `'False Start'`, `'Defensive Pass Interference'`) to narrow. Team-level: group by `pbp.penalty_team` instead of `penalty_player_id`.

**Multi-season PBP aggregation — avoid timeouts**

Multi-season joins to `players` on unfiltered `passer_player_id` can time out (30s limit). Patterns that work:

- Narrow the season window (`BETWEEN 2015 AND 2024`, not `>= 1999`).
- Always filter on an outcome flag (`pbp.touchdown = 1`, `pbp.sack = 1`, `pbp.complete_pass = 1`, `pbp.pass_attempt = 1`).
- Add a `HAVING COUNT(*) >= N` threshold to drop tiny samples.
- Filter the driver column `IS NOT NULL` before GROUP BY.
- **Keep the first SELECT narrow.** Stick to `COUNT(*)` + 1–2 AVG/SUM aggregates. Do NOT add `COUNT(DISTINCT season)`, `MIN(season)`, `MAX(season)`, or other metadata on the first pass — each extra aggregate is a full secondary pass over the filtered rows and can push a borderline query over the 30s limit. If the user wants "seasons active" or similar metadata, run a second smaller query scoped to the top-N players from the first result.

```sql
-- clutch 4th-quarter passing TDs by QB, multi-season
SELECT p.display_name,
       COUNT(*) AS clutch_tds,
       ROUND(AVG(pbp.epa), 2) AS avg_epa
FROM play_by_play pbp
JOIN players p ON p.player_gsis_id = pbp.passer_player_id
WHERE pbp.season BETWEEN 2015 AND 2024     -- narrow window
  AND pbp.qtr >= 4
  AND pbp.score_differential < 0            -- trailing
  AND pbp.touchdown = 1
  AND pbp.pass_attempt = 1                  -- excludes sacks
  AND pbp.passer_player_id IS NOT NULL
GROUP BY p.player_gsis_id, p.display_name
HAVING COUNT(*) >= 5
ORDER BY clutch_tds DESC LIMIT 20;
```

## Multi-role unions (all-purpose TDs, all-purpose yards)

Use UNION ALL in a CTE to combine multiple role-specific player-id columns into one column for grouping:

```sql
WITH all_tds AS (
  SELECT passer_player_id AS player_gsis_id, 'pass' AS td_type FROM play_by_play
    WHERE season = 2024 AND touchdown = 1 AND pass = 1
  UNION ALL
  SELECT rusher_player_id, 'rush' FROM play_by_play
    WHERE season = 2024 AND touchdown = 1 AND rush = 1
  UNION ALL
  SELECT receiver_player_id, 'rec' FROM play_by_play
    WHERE season = 2024 AND touchdown = 1 AND pass = 1
)
SELECT p.display_name, COUNT(*) AS total_tds,
       SUM(CASE WHEN td_type='pass' THEN 1 ELSE 0 END) AS pass_tds,
       SUM(CASE WHEN td_type='rush' THEN 1 ELSE 0 END) AS rush_tds,
       SUM(CASE WHEN td_type='rec'  THEN 1 ELSE 0 END) AS rec_tds
FROM all_tds JOIN players p ON p.player_gsis_id = all_tds.player_gsis_id
WHERE all_tds.player_gsis_id IS NOT NULL
GROUP BY p.player_gsis_id, p.display_name ORDER BY total_tds DESC LIMIT 20;
```

## Performance tips

- **Filter PBP on its OWN columns, not via JOIN predicates.** Prefer `WHERE pbp.season_type='POST' AND pbp.week IN (21,22)` over `JOIN games g ON g.game_id = pbp.game_id WHERE g.game_type='SB'` — the direct PBP filter narrows the scan before any join runs.
- **Pattern:** reduce PBP rows with `season`, `season_type`, `week`, `posteam`, or a player_id column FIRST. Any JOIN to `games` or `players` should come after — it runs against the already-small result set.
- Filter before joining. `SELECT … FROM play_by_play JOIN players …` on an unfiltered PBP scan will time out.
- Break complex PBP queries into parts (passing TDs, rushing TDs, receiving TDs each in a separate CTE) rather than one giant join.
- `play_by_play.season_type` = `'REG'` / `'POST'` (binary; same as game_stats/season_stats). `play_by_play.game_date` (NOT `gameday`).

## PBP + players join

```sql
SELECT p.display_name, COUNT(*) AS tds
FROM play_by_play pbp JOIN players p ON p.player_gsis_id = pbp.passer_player_id
WHERE pbp.season >= 2004 AND pbp.touchdown = 1 AND pbp.pass = 1
GROUP BY p.player_gsis_id, p.display_name ORDER BY tds DESC LIMIT 20;
```

Use this join instead of calling `search_players` to decode abbreviated `*_player_name` columns.

## Column not listed here?

The 372-column catalog is exhaustive for this table. If you need a column not mentioned above (e.g., a rarely used slot like `tackle_with_assist_2_team`, or a specific EPA breakdown), call `get_schema({"table_name": "play_by_play"})` for the full column list.
