# Postseason Guide

Playoff, Super Bowl, Wild Card, Divisional, and Conference Championship queries. Playoff encoding **differs across four tables** — getting it wrong is a top failure mode.

**When to load:** any question about playoff games, Super Bowls, wild cards, conference championships, "postseason" totals, or individual playoff performances.

## Playoff encoding cheat sheet

| Table | Column | Regular | Postseason | Notes |
|---|---|---|---|---|
| `games` | `game_type` | `'REG'` | `'WC'` / `'DIV'` / `'CON'` / `'SB'` | **Preferred discriminator. No `'POST'` value.** |
| `play_by_play` | `season_type` + `week` | `'REG'` | `'POST'` + week 18/19/20/21/22 (era-dependent — see below) | **No `game_type` column exists.** Join to `games` if you need the round name. |
| `season_stats` / `game_stats` | `season_type` | `'REG'` | `'POST'` | Binary only — no round granularity. |
| `ngs_stats` | `season_type` | `'REG'` | `'POST'` | **SB week is offset by +1 vs game_stats** (NGS skips the Pro Bowl bye). Don't `JOIN n.week = gs.week` for playoffs — silently drops Super Bowls. See NGS postseason section. |
| `qbr` | `season_type` | `'Regular'` | `'Postseason'` | **Full words, unique to this table.** |
| `snap_counts` | `game_type` | `'REG'` | `'WC'` / `'DIV'` / `'CON'` / `'SB'` | Same granularity as `games`. |
| `depth_charts` | `game_type` | `'REG'` | `'WC'` / `'DIV'` / `'CON'` / `'SB'` | Same. |

**Rule of thumb:** if you need round granularity (WC vs DIV vs SB), start from `games` (or join to it from PBP). If binary REG/POST is enough, use `season_type` on the stats tables.

## PBP week-number encoding by era

`play_by_play` has no `game_type` — playoff rounds are encoded by `week` number, and the mapping shifted when the NFL added a 17-game regular season in 2021:

| Era | WC | DIV | CONF | SB |
|---|---|---|---|---|
| 1999–2020 (16-game reg season) | week 18 | 19 | 20 | 21 |
| 2021–present (17-game reg season) | week 19 | 20 | 21 | 22 |

Always filter `AND season_type = 'POST'` in addition to the week. **The safer approach is to join to `games`** and filter on `game_type`:

```sql
FROM play_by_play pbp
JOIN games g ON g.game_id = pbp.game_id
WHERE g.game_type = 'SB'     -- or 'WC' / 'DIV' / 'CON'
```

That query works across all eras without hardcoding week numbers.

## Templates

**All Super Bowls (from `games`)**
```sql
SELECT season, gameday, away_team, away_score, home_team, home_score,
       spread_line, total_line, roof, stadium
FROM games
WHERE game_type = 'SB'
ORDER BY season DESC;
```

**Every playoff game in one season**
```sql
SELECT week, game_type, gameday, away_team, away_score, home_team, home_score,
       spread_line, result
FROM games
WHERE season = 2024 AND game_type IN ('WC','DIV','CON','SB')
ORDER BY game_type, gameday;
```

**Super Bowl plays (joined through games — era-safe)**
```sql
SELECT pbp.qtr, pbp.time, pbp.down, pbp.ydstogo, pbp.yardline_100,
       pbp.play_type, pbp.desc, pbp.epa, pbp.wpa
FROM play_by_play pbp
JOIN games g ON g.game_id = pbp.game_id
WHERE g.game_type = 'SB' AND g.season = 2023
ORDER BY pbp.play_id;
```

**Biggest WPA plays — single round, ALL-TIME**

**Filter PBP on its OWN columns (`season_type` + `week`), NOT via a JOIN to `games`.** `play_by_play` has no index on `game_id`; writing `JOIN games g WHERE g.game_type = 'SB'` forces SQLite to build a temporary covering index on the fly (~28s / 626M ops for Super Bowls alone). Filtering PBP directly runs in ~1s.

Playoff round → PBP week filter (era-safe, covers both 16-game and 17-game eras):

| Round | PBP filter |
|---|---|
| Super Bowl | `week IN (21, 22)` |
| Conference Champ. | `week IN (20, 21)` |
| Divisional | `week IN (19, 20)` |
| Wild Card | `week IN (18, 19)` |

```sql
-- All-time biggest WPA plays in a Super Bowl (runs in ~1-2s)
SELECT pbp.season, pbp.posteam, pbp.defteam,
       pbp.qtr, pbp.time, pbp.down, pbp.ydstogo, pbp.yardline_100,
       pbp.play_type, pbp.desc,
       ROUND(pbp.wpa, 4) AS wpa, ROUND(pbp.epa, 2) AS epa,
       pbp.passer_player_name, pbp.receiver_player_name, pbp.rusher_player_name
FROM play_by_play pbp
WHERE pbp.season_type = 'POST'
  AND pbp.week IN (21, 22)                     -- both eras cover Super Bowls
  AND pbp.wpa IS NOT NULL
  AND pbp.play_type IN ('pass','run','field_goal')
ORDER BY pbp.wpa DESC
LIMIT 25;
```

Want the teams' city names (e.g. "ARI vs PIT" instead of just `posteam='ARI'`)? Add a `LEFT JOIN games g ON g.game_id = pbp.game_id` AFTER the PBP filter has reduced the row count:
```sql
SELECT g.season, g.away_team, g.home_team, pbp.qtr, pbp.time,
       pbp.play_type, pbp.desc, ROUND(pbp.wpa, 4) AS wpa
FROM play_by_play pbp
LEFT JOIN games g ON g.game_id = pbp.game_id
WHERE pbp.season_type = 'POST' AND pbp.week IN (21, 22)
  AND pbp.wpa IS NOT NULL
ORDER BY pbp.wpa DESC LIMIT 25;
```
The LEFT JOIN runs against only ~9K already-filtered PBP rows — fast.

**Biggest WPA plays across ALL playoff rounds, multi-season**

Again — filter PBP directly. `season_type='POST'` is the cheap filter; it restricts PBP to ~60K rows out of 1.28M.
```sql
SELECT pbp.season, pbp.week, pbp.posteam, pbp.defteam, pbp.qtr,
       pbp.play_type, pbp.desc, ROUND(pbp.wpa, 4) AS wpa, ROUND(pbp.epa, 2) AS epa
FROM play_by_play pbp
WHERE pbp.season_type = 'POST'
  AND pbp.season BETWEEN 2015 AND 2024           -- narrow window
  AND pbp.wpa IS NOT NULL
  AND pbp.play_type IN ('pass','run','field_goal')
ORDER BY pbp.wpa DESC
LIMIT 25;
```
If the user wants round-name granularity, `LEFT JOIN games g ON g.game_id = pbp.game_id` after the PBP filter.

**Career playoff passing totals for a QB**

`season_stats` stores one row per player-season-season_type, so postseason sums are already per-year. To get a career playoff total:
```sql
SELECT p.display_name,
       COUNT(*)                      AS playoff_seasons,
       SUM(ss.games)                 AS games,
       SUM(ss.completions)           AS cmp,
       SUM(ss.attempts)              AS att,
       SUM(ss.passing_yards)         AS yds,
       SUM(ss.passing_tds)           AS td,
       SUM(ss.passing_interceptions) AS ints
FROM season_stats ss
JOIN players p ON p.gsis_id = ss.player_id
WHERE p.display_name = 'Patrick Mahomes'
  AND ss.season_type = 'POST';
```

**Individual playoff game logs (by round)**

Round-level granularity requires joining to `games`:
```sql
SELECT g.season, g.game_type AS round, g.gameday,
       gs.team, gs.opponent_team,
       gs.passing_yards, gs.passing_tds, gs.passing_interceptions,
       gs.rushing_yards, gs.rushing_tds,
       gs.fantasy_points_ppr
FROM game_stats gs
JOIN players p ON p.gsis_id = gs.player_id
JOIN games g ON g.game_id = gs.game_id          -- gs.game_id populated 2022+
WHERE p.display_name = 'Patrick Mahomes'
  AND gs.season_type = 'POST'
ORDER BY g.season DESC, g.gameday;
```

Note: `game_stats.game_id` is only populated for 2022+. For older seasons, drop the `games` join and lose round granularity, or match on `(season, week, team)` — `season_type='POST'` + the week-number era rules above.

**Biggest playoff upsets**
```sql
SELECT g.season, g.game_type, g.gameday, g.away_team, g.away_score, g.home_team, g.home_score,
       g.spread_line,
       CASE WHEN g.spread_line > 0 AND g.away_score > g.home_score THEN 'away upset'
            WHEN g.spread_line < 0 AND g.home_score > g.away_score THEN 'home upset' END AS kind,
       ABS(g.spread_line) AS line_size
FROM games g
WHERE g.game_type IN ('WC','DIV','CON','SB')
  AND ((g.spread_line > 0 AND g.away_score > g.home_score)
    OR (g.spread_line < 0 AND g.home_score > g.away_score))
ORDER BY line_size DESC
LIMIT 25;
```

**Most playoff games played (career)**
```sql
SELECT p.display_name, p.position, COUNT(*) AS playoff_games
FROM game_stats gs
JOIN players p ON p.gsis_id = gs.player_id
WHERE gs.season_type = 'POST'
GROUP BY p.gsis_id, p.display_name, p.position
ORDER BY playoff_games DESC
LIMIT 25;
```

**Playoff QBR leaders (2006–2023 only — QBR coverage ends 2023)**
```sql
SELECT p.display_name, q.season,
       ROUND(AVG(q.qbr_total), 1) AS playoff_qbr,
       SUM(q.qb_plays)            AS plays
FROM qbr q
JOIN player_ids pi ON CAST(pi.espn_id AS INTEGER) = CAST(q.player_id AS INTEGER)
JOIN players p ON p.gsis_id = pi.gsis_id
WHERE q.season_type = 'Postseason'          -- NOT 'POST' — qbr is the odd one out
  AND q.qualified = 1
GROUP BY p.gsis_id, p.display_name, q.season
ORDER BY playoff_qbr DESC
LIMIT 20;
```

## NGS postseason — week offset + partial coverage

`ngs_stats` postseason weeks do NOT line up with `game_stats` postseason weeks. NGS skips the Pro Bowl bye, so the Super Bowl row is always labeled one week higher than `game_stats` labels the same game:

| Era | game_stats SB week | NGS SB week |
|---|---|---|
| 1999–2020 | 21 | 22 |
| 2021–present | 22 | 23 |

WC/DIV/CON line up; the Super Bowl is the only offset. A naive `LEFT JOIN ngs_stats n ON n.season = gs.season AND n.week = gs.week` silently drops every Super Bowl — a common failure mode when a user asks "what's player X's NGS postseason line?" and the answer wrongly shows nulls for the biggest game.

**Safe patterns:**

```sql
-- (a) Query NGS alone for postseason, skip the game_stats join entirely:
SELECT n.season, n.week, n.targets, n.receptions, n.yards, n.avg_separation
FROM ngs_stats n JOIN players p ON p.gsis_id = n.player_gsis_id
WHERE p.display_name = 'DeVonta Smith'
  AND n.stat_type = 'receiving' AND n.season_type = 'POST'
ORDER BY n.season, n.week;
```

```sql
-- (b) If you MUST join game_stats + NGS postseason, normalize the SB week:
LEFT JOIN ngs_stats n
  ON n.player_gsis_id = gs.player_id
 AND n.season = gs.season
 AND n.season_type = 'POST'
 AND n.stat_type = 'receiving'
 AND n.week = CASE
       WHEN gs.season >= 2021 AND gs.week = 22 THEN 23   -- SB, 17-game era
       WHEN gs.season <  2021 AND gs.week = 21 THEN 22   -- SB, 16-game era
       ELSE gs.week END                                  -- WC/DIV/CON align
```

**NGS receiving is a qualified leaderboard, not full coverage.** Each playoff week charts only ~9–20 receivers league-wide (minimum routes/targets). A player with 2–4 catches in a playoff game often won't have a row — that's not a data bug, that's NGS's own threshold. If the user asks "why is X missing?", check their target volume for that game first.

## Gotchas

- **`play_by_play` has NO `game_type` column.** Filtering `WHERE game_type = 'SB'` on PBP errors out. Use `season_type='POST'` + week, or join to `games`.
- **Week numbers for playoff rounds changed in 2021.** Prefer `JOIN games g ON g.game_id = pbp.game_id WHERE g.game_type = 'SB'` over hardcoded week numbers for era-spanning queries.
- **`qbr.season_type` uses `'Regular'` / `'Postseason'`** — full words, unlike every other table.
- **`game_stats.game_id` is only populated 2022+.** Round-name queries against older seasons can't use the `games` join; drop to binary `season_type='POST'` or match on `(season, week, team)`.
- **`season_stats` postseason rows exist but with `season_type='POST'`.** Don't forget the filter — by default `season_type='REG'` is what most queries want.
- **Expanded playoffs started 2020** (6 playoff teams per conference + 1 more WC game). `games` captures this naturally via `game_type`, but be mindful when counting "WC games per year."

## Debugging checklist

- `no such column: game_type` on a PBP query → you need to join to `games` or filter by `season_type`+`week`.
- Postseason totals look half what you expect → missing `season_type='POST'` (or you hit REG-only data).
- QBR playoff query returns empty → `season_type` on qbr is `'Postseason'`, not `'POST'`.
- Getting both REG and POST rows mixed together in a "season totals" query → add `season_type = 'REG'` (or `'POST'`) to the WHERE clause.
