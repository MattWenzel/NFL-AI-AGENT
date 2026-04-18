# Player Stats Guide

Weekly and season-long stat tables, plus the supplementary usage/advanced tables (`snap_counts`, `ngs_stats`, `pfr_advanced`, `qbr`).

## Primary tables

### `game_stats` — weekly stats, all position groups (476K rows, 1999–2025)

- **ID: `player_id`** (this column holds the GSIS ID despite the name). Join: `players.gsis_id = game_stats.player_id`.
- **Team column: `team`**, opponent column: `opponent_team` (NOT `opponent`).
- **`season_type`**: `'REG'` / `'POST'`.
- Contains all offensive stats + kicker stats + fantasy points. Weekly granularity.

### `season_stats` — season totals, all position groups (49K rows, 1999–2025)

- **ID: `player_id`** (same as game_stats — GSIS ID in the `player_id` column).
- **Team column: `recent_team`** (NOT `team`). Backfilled from game_stats as the player's most common team that season.
- Also has `games` (games played) column.
- Regular season only. `season_type='REG'` filter is tidy but redundant.

### These columns DO NOT EXIST (common mental shortcuts that fail)

Every stat on these tables is **phase-scoped**. If you write the plain-English name, the query errors out. Use the correct replacement:

| ❌ You wrote | ✅ Actually | Notes |
|---|---|---|
| `sacks` | `sacks_suffered` (offensive) or `def_sacks` (defensive) | No plain `sacks` column. |
| `sack_yards` | `sack_yards_lost` (offensive) or `def_sack_yards` (defensive) | |
| `fumbles` | `sack_fumbles + rushing_fumbles + receiving_fumbles` for an offensive total | Use `COALESCE(..., 0)` around each. |
| `fumbles_lost` | `sack_fumbles_lost + rushing_fumbles_lost + receiving_fumbles_lost` | Same wrap-in-`COALESCE` rule. |
| `touchdowns` | `passing_tds + rushing_tds + receiving_tds` (offensive total) or `def_tds` (defensive) | |
| `tds` | Same as above — always phase-scoped. | |
| `rush_attempts`, `passing_attempts` | `carries`, `attempts` | |
| `receptions_yards`, `rec_yards` | `receiving_yards` | |
| `interceptions` (on offense) | `passing_interceptions` (QB threw) or `def_interceptions` (defender caught) | Ambiguous — disambiguate. |

After any `no such column` error, the next tool call must be `get_schema` for the offending table — do not re-guess.

### Shared offensive columns

`completions`, `attempts`, `passing_yards`, `passing_tds`, `passing_interceptions`, `passing_2pt_conversions`, `sacks_suffered`, `sack_yards_lost`, `sack_fumbles`, `sack_fumbles_lost`, `carries` (NOT `rush_attempts`), `rushing_yards`, `rushing_tds`, `rushing_fumbles`, `rushing_fumbles_lost`, `receptions`, `targets`, `receiving_yards`, `receiving_tds`, `receiving_fumbles`, `receiving_fumbles_lost`, `target_share`, `wopr`, `racr`, `passing_epa`, `fantasy_points`, `fantasy_points_ppr`.

**`sacks_suffered` is OFFENSIVE** — times the QB was sacked. For **defensive** sacks and all other defensive stats, see the "Defensive stats" section below — `season_stats` / `game_stats` have a full `def_*` block; PBP is only needed for play-level breakdowns.

### Shared kicker columns

`fg_made`, `fg_att`, `fg_pct`, `fg_long`, `fg_missed`, `pat_made`, `pat_att`, `pat_pct`, `pat_missed`, plus distance bands `fg_made_0_19`, `fg_made_20_29`, `fg_made_30_39`, `fg_made_40_49`, `fg_made_50_59`, `fg_made_60_` (and matching `fg_missed_X_Y`).

Filter kickers with `p.position = 'K'`. See the **fantasy** guide for the full scoring template.

### Defensive stats (on season_stats AND game_stats)

Both tables carry a **full defensive block** for every player-season/player-game — there is no separate "defensive stats" table. Query `season_stats` for season totals, `game_stats` for weekly defensive stats. Use `play_by_play` only for play-level detail (who forced the fumble on 3rd down in Q4, etc.).

**Tackles**: `def_tackles_solo`, `def_tackles_with_assist`, `def_tackle_assists`, `def_tackles_for_loss`, `def_tackles_for_loss_yards`
**Pass rush**: `def_sacks`, `def_sack_yards`, `def_qb_hits`
**Ball-hawking**: `def_interceptions`, `def_interception_yards`, `def_pass_defended`
**Fumbles**: `def_fumbles_forced`, `def_fumbles`, `fumble_recovery_own`, `fumble_recovery_yards_own`, `fumble_recovery_opp`, `fumble_recovery_yards_opp`, `fumble_recovery_tds`
**Scoring**: `def_tds`, `def_safeties`

**Templates**:

```sql
-- Sack leaders (season)
SELECT p.display_name, p.position, ss.recent_team AS team, ss.games, ss.def_sacks
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE ss.season = 2025 AND ss.season_type = 'REG' AND ss.def_sacks > 0
ORDER BY ss.def_sacks DESC LIMIT 20;

-- Interception leaders (season)
SELECT p.display_name, p.position, ss.recent_team AS team,
       ss.def_interceptions, ss.def_interception_yards, ss.def_tds
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE ss.season = 2024 AND ss.season_type = 'REG' AND ss.def_interceptions > 0
ORDER BY ss.def_interceptions DESC LIMIT 20;

-- All-around defensive profile (career totals)
SELECT p.display_name,
       SUM(ss.def_sacks) AS sacks,
       SUM(ss.def_tackles_solo) + SUM(ss.def_tackle_assists) AS combined_tackles,
       SUM(ss.def_tackles_for_loss) AS tfl,
       SUM(ss.def_interceptions) AS ints,
       SUM(ss.def_fumbles_forced) AS ff,
       SUM(ss.def_pass_defended) AS pbu,
       SUM(ss.def_tds) AS def_tds
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE p.gsis_id = '00-0029382'  -- (e.g. Aaron Donald)
  AND ss.season_type = 'REG'
GROUP BY p.gsis_id, p.display_name;
```

**When to use PBP instead**: play-level filters (e.g. "sacks in the 4th quarter trailing by 1 score", "INTs on 3rd down"), or when you need the victim / down / game context. For "top sacks in a season", use `season_stats.def_sacks` — don't aggregate PBP.

## Supplementary tables

### `snap_counts` — usage / snap share (277K rows, 2015–2025)

**The #1 source of query errors.** Rules:
- **ID**: `pfr_player_id` — bridges via `player_ids.pfr_id` (note: DIFFERENT column names; `player_ids.pfr_player_id` does NOT exist).
- **Player-name column is `player`** (NOT `player_name`, NOT `display_name`).
- **`offense_pct` is a 0–1 fraction.** Display as `ROUND(offense_pct * 100, 1)`.
- **No season totals** — there's no `week = 0` row. Compute with `AVG(offense_pct)` + `WHERE week BETWEEN 1 AND 18`.
- **Always filter by season**. Without it, joins time out.
- **Bridge joins without a season filter also time out.** Aggregate snap_counts in a CTE first, THEN join:
```sql
WITH totals AS (
  SELECT pfr_player_id, team, SUM(defense_snaps) AS total
  FROM snap_counts WHERE season = 2024
  GROUP BY pfr_player_id, team ORDER BY total DESC LIMIT 50
)
SELECT p.display_name, t.team, t.total
FROM totals t
JOIN player_ids pi ON pi.pfr_id = t.pfr_player_id
JOIN players p ON p.gsis_id = pi.gsis_id
ORDER BY t.total DESC LIMIT 20;
```

### `ngs_stats` — Next Gen Stats (27K rows, 2016–2025)

- **IDs are DIFFERENT.** `player_gsis_id` (NOT `gsis_id`), `player_display_name` (NOT `display_name`), `team_abbr` (NOT `team`).
- **`stat_type`**: `'passing'` / `'rushing'` / `'receiving'` (full words — different from `pfr_advanced`).
- **`week = 0` means season totals.** Weekly rows are week=1, 2, …

**Column catalog by stat_type** (columns are sparse; only the matching `stat_type` rows have them populated):

*Shared:* `season`, `season_type`, `week`, `player_gsis_id`, `player_display_name`, `player_position`, `team_abbr`, `stat_type`, `player_first_name`, `player_last_name`, `player_jersey_number`, `player_short_name`.

*`stat_type = 'passing'`* — 18 cols:
`attempts`, `completions`, `completion_percentage`, `expected_completion_percentage`, `completion_percentage_above_expectation` (CPOE — 0–100 scale), `pass_yards`, `pass_touchdowns`, `interceptions`, `passer_rating`, `avg_time_to_throw`, `avg_completed_air_yards`, `avg_intended_air_yards`, `avg_air_yards_differential`, `aggressiveness`, `max_completed_air_distance`, `avg_air_yards_to_sticks`, `avg_air_distance`, `max_air_distance`.

*`stat_type = 'rushing'`* — 11 cols:
`rush_attempts`, `rush_yards`, `avg_rush_yards`, `rush_touchdowns`, `efficiency`, `percent_attempts_gte_eight_defenders`, `avg_time_to_los`, `expected_rush_yards`, `rush_yards_over_expected` (RYOE), `rush_yards_over_expected_per_att`, `rush_pct_over_expected`.

*`stat_type = 'receiving'`* — 11 cols:
`targets`, `receptions`, `catch_percentage`, `yards` (NOT `receiving_yards`), `rec_touchdowns`, `avg_cushion`, `avg_separation`, `percent_share_of_intended_air_yards`, `avg_yac`, `avg_expected_yac`, `avg_yac_above_expectation`.

**Common translations:**
- passing `pass_yards` / `pass_touchdowns` ≠ season_stats `passing_yards` / `passing_tds`. Same stat, different names.
- receiving: yards column is `yards` (not `receiving_yards`).
- "Attempts" in a receiving context means `targets`. The `attempts` column exists but is NULL for receiving rows.

**Multi-season thresholds go in `HAVING`, not `WHERE`.** For "past N years with ≥ X targets":
```sql
GROUP BY player_gsis_id, player_display_name
HAVING SUM(targets) >= 200   -- applies to the total, not per-row
```
`WHERE targets >= 200` would filter individual season rows, not career totals.

### `pfr_advanced` — PFR advanced stats (7.8K rows, 2018–2025)

- **ID**: `pfr_id` — bridges via `player_ids.pfr_id`.
- **`stat_type`**: `'pass'` / `'rush'` / `'rec'` (abbreviated — different from NGS which uses full words).
- **Player-name column is `player`** (not `display_name`). **Team is `team`** (plus `tm` on some rows).

**Column catalog by stat_type** — columns are sparse, populated only when they apply to the row's `stat_type`:

*Shared (always populated):* `season`, `pfr_id`, `player`, `team`, `tm`, `age`, `pos`, `g` (games), `gs` (games started), `stat_type`, `loaded`.

*`stat_type = 'pass'`* — 31 pass-specific columns:
`pass_attempts`, `throwaways`, `spikes`, `drops`, `drop_pct`, `bad_throws`, `bad_throw_pct`, `pocket_time`, `times_blitzed`, `times_hurried`, `times_hit`, `times_pressured`, `pressure_pct`, `batted_balls`, `on_tgt_throws`, `on_tgt_pct`, `rpo_plays`, `rpo_yards`, `rpo_pass_att`, `rpo_pass_yards`, `rpo_rush_att`, `rpo_rush_yards`, `pa_pass_att`, `pa_pass_yards`, `intended_air_yards`, `intended_air_yards_per_pass_attempt`, `completed_air_yards`, `completed_air_yards_per_completion`, `completed_air_yards_per_pass_attempt`, `pass_yards_after_catch`, `pass_yards_after_catch_per_completion`, `scrambles`, `scramble_yards_per_attempt`.

*`stat_type = 'rush'`* — 10 short-name columns:
`att` (attempts), `yds` (yards), `td`, `x1d` (first downs), `ybc` (yards before contact), `ybc_att` (YBC per attempt), `yac` (yards after contact), `yac_att`, `brk_tkl` (broken tackles), `att_br` (attempts per broken tackle).

*`stat_type = 'rec'`* — 13 short-name columns:
`tgt` (targets), `rec` (receptions), `yds`, `td`, `x1d`, `ybc_r`, `yac_r`, `adot` (average depth of target), `rec_br` (receptions per broken tackle), `drop`, `drop_percent`, `int` (interceptions thrown at), `rat` (QB passer rating when targeted).

**Columns NOT in `pfr_advanced`:** yards per route run (`yprr`), routes run, target separation (those live in `ngs_stats`: `avg_separation`, `avg_cushion`). Don't guess `yprr`/`ypc`/`ypr` — the table doesn't have them.

### `qbr` — ESPN QBR (9.6K rows, 2006–2023)

- **ID**: `player_id` (ESPN ID, integer-stringy) — bridges via `player_ids.espn_id` with `CAST(pi.espn_id AS INTEGER) = CAST(q.player_id AS INTEGER)`.
- **`game_week` is INTEGER** (1, 2, …). Also has `week_text` ("Week 1"/"Wild Card").
- **No season-total rows exist.** Compute with `AVG(qbr_total)`, `SUM(pts_added)` GROUP BY player+season.
- **`season_type`**: `'Regular'` / `'Postseason'` (NOT `REG`/`POST` — different from every other table).
- **Includes trick-play non-QBs** (WRs, RBs) with tiny samples and inflated QBR. Filter `qualified = 1` or `qb_plays >= 200`.
- **`name_display`** (NOT `player_name`).
- **Coverage ends 2023.** No 2024–2025 QBR data. For recent QB efficiency, use `passing_epa` / `passing_cpoe` on `season_stats` or `completion_percentage_above_expectation` on `ngs_stats` instead.

## Bridge joins — exact SQL

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

-- ngs → players
JOIN players p ON p.gsis_id = n.player_gsis_id
```

## Templates

**Season leaderboard**
```sql
SELECT p.display_name, ss.recent_team AS team, ss.passing_yards, ss.passing_tds,
       ss.passing_interceptions, ss.fantasy_points_ppr
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE p.position = 'QB' AND ss.season = 2024
ORDER BY ss.passing_yards DESC LIMIT 20;
```

**Career stats season-by-season (one player, all years)**
Don't call `search_players` first for a well-known full name — just filter by `p.display_name`:
```sql
SELECT ss.season, ss.recent_team AS team, ss.games,
       ss.completions, ss.attempts, ss.passing_yards, ss.passing_tds, ss.passing_interceptions,
       ss.sacks_suffered,
       ss.carries, ss.rushing_yards, ss.rushing_tds,
       ss.receptions, ss.receiving_yards, ss.receiving_tds,
       COALESCE(ss.sack_fumbles_lost, 0) + COALESCE(ss.rushing_fumbles_lost, 0)
         + COALESCE(ss.receiving_fumbles_lost, 0) AS fumbles_lost,
       ss.fantasy_points_ppr
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE p.display_name = 'Matthew Stafford' AND ss.season_type = 'REG'
ORDER BY ss.season;
```

**Player game log (single player, single season)**
```sql
SELECT gs.week, gs.team, gs.opponent_team,
       gs.passing_yards, gs.passing_tds, gs.passing_interceptions,
       gs.fantasy_points_ppr
FROM game_stats gs
WHERE gs.player_id = '00-0033873' AND gs.season = 2024 AND gs.season_type = 'REG'
ORDER BY gs.week;
```

**Multi-player comparison**
```sql
SELECT p.display_name, ss.carries, ss.rushing_yards, ss.rushing_tds
FROM season_stats ss JOIN players p ON p.gsis_id = ss.player_id
WHERE p.display_name IN ('Derrick Henry', 'Saquon Barkley') AND ss.season = 2024;
```

**NGS CPOE leaders (season)**
```sql
SELECT p.display_name, n.pass_yards, n.pass_touchdowns, n.completion_percentage_above_expectation
FROM ngs_stats n JOIN players p ON p.gsis_id = n.player_gsis_id
WHERE n.season = 2024 AND n.week = 0 AND n.stat_type = 'passing'
ORDER BY n.completion_percentage_above_expectation DESC LIMIT 15;
```

## Gotchas

- `season_type`: `'REG'` / `'POST'` on game_stats/season_stats/ngs_stats/play_by_play. `'Regular'` / `'Postseason'` on qbr. `'REG'`/`'WC'`/`'DIV'`/`'CON'`/`'SB'` on games/snap_counts/depth_charts (different column: `game_type`).
- `game_stats.player_id` / `season_stats.player_id` hold GSIS IDs. `snap_counts.pfr_player_id` holds PFR IDs. Bridge via `player_ids`.
- Always alias tables (ss for season_stats, gs for game_stats, sc for snap_counts, n for ngs_stats, pa for pfr_advanced, q for qbr, p for players, pi for player_ids) — columns like `season`, `week`, `team` exist in multiple tables.
- Call `get_schema` before the first query against `pfr_advanced`, `ngs_stats`, `qbr`, `draft_picks`, or `combine`. Skip it for game_stats, season_stats, games, play_by_play, players.
