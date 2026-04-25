# Games Guide

Schedule, scores, weather, betting lines, and how to use `games` as the anchor for game-level joins.

## `games` — 7.3K rows, 1999–2025

- **Primary key: `game_id`** (format `{season}_{week}_{away}_{home}`, e.g. `2024_01_KC_BAL`).
- Key columns:
  - Date: **`gameday`** (NOT `game_date` — PBP uses `game_date`, games uses `gameday`).
  - Teams: `home_team`, `away_team`, `home_coach`, `away_coach`.
  - Scoring: `home_score`, `away_score`, `result` (= `home_score − away_score`), `total` (= sum).
  - **`game_type`**: `'REG'` / `'WC'` / `'DIV'` / `'CON'` / `'SB'` (granular — NOT `'POST'`).
  - Betting: `spread_line`, `total_line`, `away_moneyline`, `home_moneyline`.
  - Weather / venue: `temp` (°F), `wind` (mph), `roof` (`outdoors`/`dome`/`closed`/`open`), `surface`, `stadium`, `stadium_id`.
  - Other: `week`, `weekday`, `gametime`, `div_game` (1 if divisional).

## Betting line conventions

- **`spread_line` is from the HOME team's perspective: positive = home favored.** `spread_line = 9.5` means home favored by 9.5; `spread_line = -3` means away favored by 3.
- **Upset logic**:
  - Away upset (underdog road team wins): `spread_line > 0 AND away_score > home_score`.
  - Home upset (underdog home team wins): `spread_line < 0 AND home_score > away_score`.
- `total_line` is the over/under. `total > total_line` = over hit.
- `result = home_score − away_score`. Positive = home won.

## Filter examples

- All playoffs: `game_type IN ('WC','DIV','CON','SB')`. **No `'POST'` value** — that exists on season_type tables (game_stats, season_stats, ngs_stats, play_by_play).
- Outdoor / weather games: `roof IN ('outdoors','open')`.
- Divisional matchups: `div_game = 1`.
- Cold-weather: `temp <= 32 AND roof IN ('outdoors','open')`.
- Primetime: `weekday IN ('Thursday','Sunday','Monday') AND gametime >= '20:00:00'` (approximate — `gametime` is local kickoff).

## Joins from `games`

```sql
-- games → game_stats (one row per player per game)
JOIN game_stats gs ON gs.game_id = g.game_id

-- games → snap_counts
JOIN snap_counts sc ON sc.game_id = g.game_id

-- games → play_by_play (cross-database; PBP auto-attaches)
JOIN play_by_play pbp ON pbp.game_id = g.game_id
```

## Templates

**Week's slate**
```sql
SELECT gameday, weekday, gametime, away_team, away_score, home_team, home_score,
       spread_line, total_line, roof, temp, wind
FROM games
WHERE season = 2024 AND week = 1 AND game_type = 'REG'
ORDER BY gameday, gametime;
```

**Team schedule + result for a season**
```sql
SELECT g.week, g.gameday,
       CASE WHEN g.home_team = 'KC' THEN g.away_team ELSE g.home_team END AS opponent,
       CASE WHEN g.home_team = 'KC' THEN 'home' ELSE 'away' END AS loc,
       CASE WHEN g.home_team = 'KC' THEN g.home_score ELSE g.away_score END AS kc_score,
       CASE WHEN g.home_team = 'KC' THEN g.away_score ELSE g.home_score END AS opp_score,
       g.spread_line
FROM games g
WHERE g.season = 2024 AND (g.home_team = 'KC' OR g.away_team = 'KC')
ORDER BY g.week;
```

**Biggest upsets of a season (by spread)**
```sql
SELECT g.gameday, g.away_team, g.away_score, g.home_team, g.home_score,
       g.spread_line,
       CASE WHEN g.spread_line > 0 AND g.away_score > g.home_score THEN 'away upset'
            WHEN g.spread_line < 0 AND g.home_score > g.away_score THEN 'home upset' END AS kind,
       ABS(g.spread_line) AS line_size
FROM games g
WHERE g.season = 2024
  AND ((g.spread_line > 0 AND g.away_score > g.home_score)
    OR (g.spread_line < 0 AND g.home_score > g.away_score))
ORDER BY line_size DESC LIMIT 20;
```

**Weather-game team stats (cold + outdoor)**
```sql
SELECT g.gameday, g.home_team, g.away_team, g.temp, g.wind,
       gs.team, gs.passing_yards, gs.rushing_yards
FROM games g JOIN game_stats gs ON gs.game_id = g.game_id
WHERE g.season = 2024 AND g.temp <= 32 AND g.roof IN ('outdoors','open')
  AND gs.position = 'QB' -- or any filter
ORDER BY g.temp ASC;
```

**PBP anchored by games (e.g. all plays in one game)**
```sql
SELECT pbp.qtr, pbp.time, pbp.desc, pbp.epa
FROM play_by_play pbp
WHERE pbp.game_id = '2024_22_KC_PHI'
ORDER BY pbp.play_id;
```

**Moneyline value — biggest +ML cashes**
```sql
-- away dogs that won outright
SELECT gameday, away_team, home_team, away_score, home_score,
       away_moneyline, spread_line
FROM games
WHERE season = 2024
  AND away_moneyline > 0          -- away team was the underdog
  AND away_score > home_score     -- …and won
ORDER BY away_moneyline DESC
LIMIT 20;
```
Moneyline convention: positive = underdog payout odds (e.g. `+250` pays $250 on $100 risked). Negative = favorite price (e.g. `-160` requires $160 to win $100). The favored side has the negative moneyline; the dog has the positive.

## Playoff-round cheatsheet

Use `games.game_type` for round granularity:

| Round | `game_type` |
|---|---|
| Regular season | `'REG'` |
| Wild Card | `'WC'` |
| Divisional | `'DIV'` |
| Conference Championship | `'CON'` |
| Super Bowl | `'SB'` |

**There is no `'POST'` value in `games`.** All playoffs: `game_type IN ('WC','DIV','CON','SB')`. The same `game_type` values appear on `snap_counts` and `depth_charts`.

`play_by_play` is the odd one out — it has **`season_type`** (`'REG'`/`'POST'`) plus a `week` number for round identification (week numbers shift by era). Prefer `JOIN games g ON g.game_id = pbp.game_id WHERE g.game_type = 'SB'` over hardcoded week numbers. Full playoff encoding cheat sheet: `get_guide({"topic": "postseason"})`.

## Gotchas

- `gameday` ≠ `game_date` (games uses the first, play_by_play uses the second).
- `game_type` values are granular (`REG`/`WC`/`DIV`/`CON`/`SB`). There is no `'POST'` value in this table.
- `spread_line` sign: **positive = home favored**. Easy to flip by accident.
- `roof` values: `outdoors`, `dome`, `closed`, `open`. "Closed" = retractable roof closed, "open" = retractable roof open.
- `home_coach` / `away_coach` are head coach names — no FK to any other table.
- For a team's season record, group `CASE WHEN` by whether the team was home vs. away and whether they outscored the other.
