# Drives Guide

Drive-level analytics against `play_by_play`. Use this guide for any question about drive length, drive outcomes, time of possession, scoring-drive rates, three-and-outs, or explosive drives.

**When to load:** drive-level aggregation ("longest drives", "three-and-out rate", "scoring drive %", "TOP leaders", "drives ending in a TD").

## The one rule

`drive_*` columns repeat on every play of the same drive. A 12-play drive produces 12 identical rows. **Collapse to one row per drive with a WHERE filter — never `DISTINCT`.**

```sql
WHERE drive_play_id_started = play_id   -- recommended: first play of each drive
-- or
WHERE drive_play_id_ended   = play_id   -- last play of each drive
```

`DISTINCT` looks like it works but silently collapses different drives that happen to share the same `drive_time_of_possession + drive_play_count + fixed_drive_result` (common for two 3-play punts at 1:30 apart). Use the filter.

## Drive columns on `play_by_play`

| Column | Meaning |
|---|---|
| `fixed_drive` | Integer drive number within the game (1, 2, 3, …) |
| `fixed_drive_result` | Text outcome (see enum below) |
| `drive_play_count` | Plays in the drive |
| `drive_time_of_possession` | TEXT `'M:SS'` or `'MM:SS'` — must be parsed (see CTE below) |
| `drive_first_downs` | First downs gained on the drive |
| `drive_inside20` | 1 if drive entered red zone |
| `drive_ended_with_score` | 1 if drive resulted in any score (TD/FG/2pt/safety) |
| `drive_yards_penalized` | Penalty yards gained by the offense on the drive |
| `drive_start_yard_line` / `drive_end_yard_line` | Text, e.g. `'KC 25'` |
| `drive_start_transition` / `drive_end_transition` | How the drive began/ended (`KICKOFF`, `PUNT`, `INTERCEPTION`, `FUMBLE`, `TOUCHDOWN`, `FIELD_GOAL`, `DOWNS`, `MISSED_FG`, `END_HALF`, `END_GAME`) |
| `drive_quarter_start` / `drive_quarter_end` | Quarter the drive began/ended |
| `drive_play_id_started` / `drive_play_id_ended` | `play_id` of the first / last play of the drive (use in WHERE to collapse) |
| `drive_game_clock_start` / `drive_game_clock_end` | Text `'MM:SS'` |
| `drive_real_start_time` | Wallclock ISO string |

## `fixed_drive_result` enum

Nine distinct values:

- `Touchdown` — offensive TD
- `Opp touchdown` — pick-6 or fumble-return TD against the offense
- `Field goal`
- `Missed field goal`
- `Punt`
- `Turnover` — INT or fumble lost (not scored)
- `Turnover on downs`
- `Safety`
- `End of half` — time or half ran out

Scoring drives = `fixed_drive_result IN ('Touchdown', 'Field goal')`. Negative drives (for the offense) = `('Turnover', 'Turnover on downs', 'Opp touchdown', 'Safety', 'Missed field goal')`.

## Canonical MM:SS → seconds parser

`drive_time_of_possession` is TEXT, so ORDER BY on it sorts lexicographically (not numerically). Use this parser in a CTE:

```sql
WITH drives AS (
  SELECT
    season, week, game_id, posteam, defteam,
    fixed_drive, fixed_drive_result,
    drive_play_count, drive_first_downs,
    drive_time_of_possession,
    drive_start_transition, drive_end_transition,
    -- 'MM:SS' → total seconds
    (CAST(substr(drive_time_of_possession, 1, instr(drive_time_of_possession, ':') - 1) AS INTEGER) * 60
     + CAST(substr(drive_time_of_possession, instr(drive_time_of_possession, ':') + 1) AS INTEGER)) AS top_seconds
  FROM play_by_play
  WHERE season BETWEEN 2016 AND 2025
    AND drive_play_id_started = play_id          -- one row per drive
    AND drive_time_of_possession IS NOT NULL
    AND drive_time_of_possession LIKE '%:%'       -- skip malformed rows
)
```

Reuse this CTE as a building block for every drive-duration query below.

## Templates

**Longest drives (by time of possession)**
```sql
WITH drives AS (
  SELECT season, week, game_id, posteam, defteam,
         drive_time_of_possession, drive_play_count, drive_first_downs,
         fixed_drive_result,
         (CAST(substr(drive_time_of_possession, 1, instr(drive_time_of_possession, ':') - 1) AS INTEGER) * 60
          + CAST(substr(drive_time_of_possession, instr(drive_time_of_possession, ':') + 1) AS INTEGER)) AS top_seconds
  FROM play_by_play
  WHERE season BETWEEN 2016 AND 2025
    AND drive_play_id_started = play_id
    AND drive_time_of_possession IS NOT NULL
    AND drive_time_of_possession LIKE '%:%'
)
SELECT season, week, posteam AS offense, defteam AS defense,
       drive_time_of_possession, drive_play_count, drive_first_downs, fixed_drive_result
FROM drives
ORDER BY top_seconds DESC, drive_play_count DESC
LIMIT 25;
```

**Scoring-drive rate by team (single season)**
```sql
SELECT posteam,
       COUNT(*) AS drives,
       SUM(CASE WHEN fixed_drive_result IN ('Touchdown','Field goal') THEN 1 ELSE 0 END) AS scoring,
       ROUND(100.0 * SUM(CASE WHEN fixed_drive_result IN ('Touchdown','Field goal') THEN 1 ELSE 0 END)
             / COUNT(*), 1) AS scoring_pct,
       SUM(CASE WHEN fixed_drive_result = 'Touchdown' THEN 1 ELSE 0 END) AS td_drives
FROM play_by_play
WHERE season = 2024
  AND drive_play_id_started = play_id
GROUP BY posteam
ORDER BY scoring_pct DESC;
```

**Three-and-out rate**
```sql
SELECT posteam,
       COUNT(*) AS drives,
       SUM(CASE WHEN drive_play_count = 3 AND fixed_drive_result = 'Punt' THEN 1 ELSE 0 END) AS three_and_outs,
       ROUND(100.0 * SUM(CASE WHEN drive_play_count = 3 AND fixed_drive_result = 'Punt' THEN 1 ELSE 0 END)
             / COUNT(*), 1) AS pct
FROM play_by_play
WHERE season = 2024
  AND drive_play_id_started = play_id
GROUP BY posteam
ORDER BY pct ASC;
```

**Drive outcomes breakdown (team x season)**
```sql
SELECT posteam, fixed_drive_result,
       COUNT(*) AS drives,
       ROUND(AVG(drive_play_count), 1) AS avg_plays,
       ROUND(AVG(drive_first_downs), 2) AS avg_first_downs
FROM play_by_play
WHERE season = 2024
  AND drive_play_id_started = play_id
GROUP BY posteam, fixed_drive_result
ORDER BY posteam, drives DESC;
```

**Explosive drives (many first downs)**
```sql
SELECT season, week, posteam, defteam,
       drive_play_count, drive_first_downs,
       drive_time_of_possession, fixed_drive_result
FROM play_by_play
WHERE season = 2024
  AND drive_play_id_started = play_id
  AND drive_first_downs >= 6
ORDER BY drive_first_downs DESC, drive_play_count DESC
LIMIT 25;
```

**Two-minute drives ending in a score**
```sql
SELECT season, week, posteam, defteam,
       drive_play_count, drive_time_of_possession,
       drive_start_transition, fixed_drive_result
FROM play_by_play
WHERE season = 2024
  AND drive_play_id_started = play_id
  AND drive_quarter_start IN (2, 4)          -- end of half situations
  AND fixed_drive_result IN ('Touchdown', 'Field goal')
ORDER BY season DESC, week DESC;
```

## Gotchas

- **Filter is mandatory.** `drive_play_id_started = play_id` (or the `_ended` variant) collapses to one-row-per-drive. Without it, aggregates are ~14× inflated.
- **`drive_time_of_possession` is TEXT.** `ORDER BY drive_time_of_possession` sorts `'9:44'` after `'10:12'` (lexicographic). Always parse to seconds first.
- **`fixed_drive` resets per game.** It's a per-game index, not a season-wide ID. To identify a unique drive across the DB, use `(game_id, fixed_drive)`.
- **Kneel-downs count as a drive.** Victory-formation drives have `fixed_drive_result = 'End of half'` and `drive_play_count` of 1–3. Filter them out with `drive_time_of_possession != '0:00'` when you need "real" drives.
- **No `season_type` filter defaults to regular-season-only behavior** — `play_by_play` includes POST weeks. If you want regular season only, add `AND season_type = 'REG'`.
- **`drive_ended_with_score` includes opponent touchdowns (pick-6, fumble-6).** If you want "offense scored," use `fixed_drive_result IN ('Touchdown','Field goal')` instead.

## Debugging checklist

- Got 14× the drive count you expected? → Missing `drive_play_id_started = play_id` filter.
- ORDER BY time-of-possession gives `'9:42'` above `'10:11'`? → Need the MM:SS → seconds cast.
- `fixed_drive_result` NULL values? → Filter `WHERE fixed_drive_result IS NOT NULL` or join to `games` to see if the game has incomplete data.
- Query times out? → Always include a `season` filter. `play_by_play` has no indexes; full-table scans will hit the 30-second limit.
