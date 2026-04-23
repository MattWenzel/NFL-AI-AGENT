# Fantasy Football Guide

Use this guide for any fantasy-scoring query: leaderboards, weekly points, custom scoring, kicker rankings, "top X fantasy seasons."

## Pre-computed points

`game_stats` and `season_stats` both have:
- `fantasy_points` — standard (non-PPR)
- `fantasy_points_ppr` — full-PPR (1.0 per reception)

Both include fumble and INT penalties. Use them for leaderboards and ranking offensive players.

Half-PPR (industry default) is not stored — compute as `fantasy_points + 0.5 * receptions` or use the manual formula below.

### Manual Half-PPR calculation (verify or custom-scoring)

When a user wants to see the scoring broken out or verify `fantasy_points_ppr`, use this formula. Matches `fantasy_points_ppr` minus the half-reception adjustment, and keeps every negative category visible:

```sql
ROUND(
    (COALESCE(gs.passing_yards, 0)         * 0.04)    -- 1 pt / 25 pass yds
  + (COALESCE(gs.passing_tds, 0)           * 4)
  + (COALESCE(gs.passing_interceptions, 0) * -1)
  + (COALESCE(gs.rushing_yards, 0)         * 0.1)
  + (COALESCE(gs.rushing_tds, 0)           * 6)
  + (COALESCE(gs.receiving_yards, 0)       * 0.1)
  + (COALESCE(gs.receiving_tds, 0)         * 6)
  + (COALESCE(gs.receptions, 0)            * 0.5)     -- half-PPR
  + (COALESCE(gs.passing_2pt_conversions, 0)  * 2)
  + (COALESCE(gs.rushing_2pt_conversions, 0)  * 2)
  + (COALESCE(gs.receiving_2pt_conversions, 0) * 2)
  - ((COALESCE(gs.sack_fumbles_lost, 0)
      + COALESCE(gs.rushing_fumbles_lost, 0)
      + COALESCE(gs.receiving_fumbles_lost, 0)) * 2)   -- −2 per fumble lost
, 2) AS half_ppr
```

Works the same swapping `gs` → `ss` for season totals. Every column is wrapped in `COALESCE(…, 0)` — one NULL in the sum will zero out the whole expression otherwise.

## Scoring reference (half-PPR, industry standard)

| Category | Points |
|----------|--------|
| Passing yards | 1 pt / 25 yds |
| Passing TD | 4 pts |
| Passing INT | **−1 pt** |
| 2-pt conversion | 2 pts |
| Rushing / Receiving yards | 1 pt / 10 yds |
| Rush / Rec TD | 6 pts |
| Receptions | 0.5 pts (half) or 1 pt (full PPR) |
| Fumbles lost | **−2 pts** |
| FG 0–39 yds | 3 pts |
| FG 40–49 yds | 4 pts |
| FG 50+ yds | 5 pts |
| FG missed | −1 pt |
| XP made | 1 pt |
| XP missed | −1 pt |

## Default output columns

### Mixed-position leaderboards ("top X fantasy seasons", "best fantasy years ever")

When the user does NOT specify a position, use this universal column set:

`display_name`, `position`, `season`, `recent_team` (team), `games`, `passing_yards`, `passing_tds`, `rushing_yards`, `rushing_tds`, `receptions`, `receiving_yards`, `receiving_tds`, `fumbles_lost`, `fantasy_points_ppr`

This is the minimum for a readable leaderboard that mixes QBs, RBs, WRs, TEs. Counting stats for every phase of play + the final points total. **Don't include** `targets`, `completions`, `attempts`, `carries`, or `passing_interceptions` in a mixed leaderboard — they're noise when positions differ row-to-row. Use the all-position template below.

### Single-position leaderboards (user says QB / RB / WR / TE)

Use the per-position columns — they carry position-relevant detail:

- **QB**: `passing_yards`, `passing_tds`, `passing_interceptions`, `completions`, `attempts`, `carries`, `rushing_yards`, `rushing_tds`, `(sack_fumbles_lost + rushing_fumbles_lost) AS fumbles_lost`
- **RB**: `carries`, `rushing_yards`, `rushing_tds`, `receptions`, `receiving_yards`, `receiving_tds`, `(rushing_fumbles_lost + receiving_fumbles_lost) AS fumbles_lost`
- **WR / TE**: `receptions`, `targets`, `receiving_yards`, `receiving_tds`, `receiving_fumbles_lost AS fumbles_lost`
- **K**: `fg_made`, `fg_att`, `fg_long`, `fg_made_0_19`, `fg_made_20_29`, `fg_made_30_39`, `fg_made_40_49`, `fg_made_50_59`, `fg_made_60_`, `pat_made`, `pat_missed`, `fg_missed` + the computed kicker `fantasy_points` (see template below)

When showing breakdowns or building custom scoring, **always include fumbles lost and passing INTs** — the most commonly forgotten negative categories. Use `COALESCE(..., 0)` around each fumble column.

## KICKERS — special handling (read carefully)

**Do NOT use `fantasy_points` or `fantasy_points_ppr` for kickers.** Those columns reflect skill-position scoring only and are ~0.0 for real kickers — using them returns a garbage leaderboard.

Two non-negotiable rules:

1. **Filter `p.position = 'K'`** from the `players` table. Without this, emergency-kicker RBs/QBs with `fg_att > 0` outrank every real kicker.
2. **Copy this SQL template verbatim** — do not paraphrase the penalty term. Penalty is `- ss.fg_missed - ss.pat_missed` (NOT `fg_att`, NOT `pat_att`).

### Template: Kicker fantasy leaderboard (multi-season)

```sql
SELECT p.display_name, ss.season, ss.recent_team AS team,
       ss.fg_made, ss.fg_att, ss.fg_missed, ss.fg_long,
       ss.pat_made, ss.pat_missed,
       (ss.fg_made_0_19 * 3 + ss.fg_made_20_29 * 3 + ss.fg_made_30_39 * 3
        + ss.fg_made_40_49 * 4 + ss.fg_made_50_59 * 5 + ss.fg_made_60_ * 5
        + ss.pat_made * 1
        - ss.fg_missed * 1 - ss.pat_missed * 1) AS fantasy_points
FROM season_stats ss JOIN players p ON p.player_gsis_id = ss.player_gsis_id
WHERE p.position = 'K'
  AND ss.season_type = 'REG'
  AND ss.season BETWEEN <START_SEASON> AND <END_SEASON>   -- fill in user's range; leave both out for all-time
  AND ss.fg_att > 0
ORDER BY fantasy_points DESC LIMIT 20
```

### Template: Weekly kicker points (one player, one season)

Swap `season_stats ss` for `game_stats gs` and the `fg_made_X_Y` / `pat_made` / `fg_missed` / `pat_missed` columns all exist on `game_stats` too. Group by nothing — one row per week.

## Template: Top X fantasy seasons — all positions, any year range (the "default leaderboard")

Use this when the user asks for "top fantasy seasons", "best fantasy years ever", "top 100 PPR seasons", etc. without specifying a position.

```sql
SELECT p.display_name, p.position, ss.season, ss.recent_team AS team, ss.games,
       ss.passing_yards, ss.passing_tds,
       ss.rushing_yards, ss.rushing_tds,
       ss.receptions, ss.receiving_yards, ss.receiving_tds,
       COALESCE(ss.sack_fumbles_lost, 0) + COALESCE(ss.rushing_fumbles_lost, 0)
         + COALESCE(ss.receiving_fumbles_lost, 0) AS fumbles_lost,
       ss.fantasy_points_ppr
FROM season_stats ss JOIN players p ON p.player_gsis_id = ss.player_gsis_id
WHERE p.position IN ('QB', 'RB', 'WR', 'TE')
  AND ss.season_type = 'REG'
  AND ss.fantasy_points_ppr IS NOT NULL
ORDER BY ss.fantasy_points_ppr DESC LIMIT 100;
```

## Template: Single-position season-long leaderboard

```sql
SELECT p.display_name, p.position, ss.recent_team AS team,
       ss.games, ss.passing_yards, ss.passing_tds, ss.rushing_yards, ss.rushing_tds,
       ss.receptions, ss.receiving_yards, ss.receiving_tds,
       COALESCE(ss.sack_fumbles_lost, 0) + COALESCE(ss.rushing_fumbles_lost, 0)
         + COALESCE(ss.receiving_fumbles_lost, 0) AS fumbles_lost,
       ss.fantasy_points_ppr
FROM season_stats ss JOIN players p ON p.player_gsis_id = ss.player_gsis_id
WHERE p.position = 'RB'         -- or 'QB', 'WR', 'TE'
  AND ss.season = 2024
  AND ss.season_type = 'REG'
ORDER BY ss.fantasy_points_ppr DESC LIMIT 20
```

## Template: Weekly fantasy leaders (any position)

```sql
SELECT gs.week, p.display_name, gs.team, gs.opponent_team, gs.fantasy_points_ppr
FROM game_stats gs JOIN players p ON p.player_gsis_id = gs.player_gsis_id
WHERE gs.season = 2024 AND gs.season_type = 'REG'
  AND p.position IN ('QB', 'RB', 'WR', 'TE')
ORDER BY gs.fantasy_points_ppr DESC LIMIT 25
```

## Gotchas

- `season_stats.recent_team` is the team column (NOT `team`). Backfilled from game_stats (most common team per player-season).
- `game_stats.opponent_team` (NOT `opponent`).
- `season_type` values are `'REG'` / `'POST'` on these tables (NOT `'Regular'`).
- `season_stats` holds ONLY regular-season rows, but keep the `season_type='REG'` filter for clarity and forward-compat.
- For custom scoring expressions, wrap every fumble column in `COALESCE(..., 0)` — NULLs will zero out the whole row's sum.
- 2-pt conversions live in `passing_2pt_conversions`, `rushing_2pt_conversions`, `receiving_2pt_conversions`.
- IDs: join `players.player_gsis_id = season_stats.player_gsis_id` — both columns hold the GSIS ID (`00-00…` format).
