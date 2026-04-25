# Contracts Guide

Salary, APY, guaranteed money, year-by-year cap hits. Two tables:

- **`contracts`** (51K rows) — one row per contract (top-line deal terms).
- **`contracts_cap_breakdown`** (302K rows) — one row per contract × cap-year (detailed year-by-year cap hits).

For "what's player X making," use `contracts`. For "year-by-year cap hits across the life of the deal" or "team salary cap for year Y," use `contracts_cap_breakdown`.

## CRITICAL: Units and magnitudes

- **`apy`, `value`, `guaranteed`, `inflated_apy`, `inflated_value`, `inflated_guaranteed` are in MILLIONS of dollars.** Dak Prescott's `apy = 60.0` means $60M/year, not $60.
- `cap_number`, `base_salary`, `prorated_bonus`, etc. on `contracts_cap_breakdown` are in **dollars** (not millions). A $45M cap hit shows as `cap_number = 45000000`.
- `cap_percent` and `apy_cap_pct` are **decimal fractions**. `0.032` = 3.2% of the cap, not 3.2%.
- `team` on `contracts` is the full team name (`'Cowboys'`, `'Bengals'`) — NOT the abbreviation. Don't compare to `games.home_team` directly; join via `players.latest_team` or use `team` for display only.

## `contracts` — top-line deal info

**ID columns:**
- `player_gsis_id` (93% populated) — direct join to `players.player_gsis_id`.
- `otc_id` — OverTheCap's ID. Present on every row, but FK-enforced ~68% of the time (the other ~32% are coaches, retired players, and non-player entries that don't exist in `players`). **Use `LEFT JOIN`** when joining contracts to players if you want to preserve those rows.

**Key columns:**

| Column | Meaning |
|---|---|
| `player` | Display name from OTC (may differ slightly from `players.display_name`) |
| `position` | Contract position label (OTC convention) |
| `team` | Full team name (e.g. `'Cowboys'`) |
| `is_active` | TRUE = currently in force. Filter by this for "current contracts." |
| `year_signed` | Calendar year signed |
| `years` | Length of deal |
| `value` | Total contract value in millions |
| `apy` | Average per year in millions |
| `guaranteed` | Guaranteed money in millions |
| `apy_cap_pct` | APY as decimal fraction of the year-signed cap |
| `inflated_apy` | APY inflation-adjusted to current dollars |
| `cols` | Raw STRUCT array of year-by-year details — **prefer `contracts_cap_breakdown` instead**. Only use `cols` for debugging; don't `cols[1].cap_number` in answers. |

## `contracts_cap_breakdown` — year-by-year cap detail

One row per contract × cap-year. This is the flat, queryable view of what lived in `contracts.cols`.

**ID columns:**
- `player_gsis_id` (97% populated) — direct join to `players`.
- `otc_id` — links back to the parent `contracts` row.
- `cap_year` — VARCHAR, values like `'2024'`, `'2025'` (not `INT`).

**Key columns** (all dollars, not millions):

| Column | Meaning |
|---|---|
| `cap_year` | Cap year for this row |
| `team` | Team holding the cap hit this year |
| `base_salary` | Base salary |
| `prorated_bonus` | Allocated signing-bonus proration |
| `roster_bonus` | Roster bonus |
| `guaranteed_salary` | Fully-guaranteed portion of salary |
| `cap_number` | Total cap hit this year |
| `cap_percent` | Cap number as decimal fraction of league cap |
| `cash_paid` | Cash actually paid this year |
| `workout_bonus` / `other_bonus` / `option_bonus` | Other bonus buckets |
| `per_game_roster_bonus` | Per-game active-roster bonus pool |

## Templates

**Top-paid active players at a position (by APY)**
```sql
SELECT p.display_name, c.team, c.apy, c.years, c.value, c.guaranteed
FROM contracts c
LEFT JOIN players p ON p.player_gsis_id = c.player_gsis_id
WHERE c.is_active = TRUE AND c.position = 'QB'
ORDER BY c.apy DESC LIMIT 20;
```

Note the **LEFT JOIN** — if the contract row is a non-player entry (coach, retired), `p.display_name` is NULL but we still see the contract. Fall back to `c.player` in presentation if needed.

**A specific player's full contract history**
```sql
SELECT c.year_signed, c.team, c.years, c.value, c.apy, c.guaranteed, c.is_active
FROM contracts c
JOIN players p ON p.player_gsis_id = c.player_gsis_id
WHERE p.display_name = 'Patrick Mahomes'
ORDER BY c.year_signed;
```

**Year-by-year cap hits for one player**
```sql
SELECT cb.cap_year, cb.team,
       cb.cap_number, cb.cap_percent,
       cb.base_salary, cb.prorated_bonus, cb.cash_paid
FROM contracts_cap_breakdown cb
JOIN players p ON p.player_gsis_id = cb.player_gsis_id
WHERE p.display_name = 'Patrick Mahomes'
ORDER BY cb.cap_year;
```

**Top cap hits for a specific year, any team**
```sql
SELECT p.display_name, p.position, cb.team,
       cb.cap_number, cb.cap_percent * 100 AS cap_pct
FROM contracts_cap_breakdown cb
JOIN players p ON p.player_gsis_id = cb.player_gsis_id
WHERE cb.cap_year = '2025'
ORDER BY cb.cap_number DESC LIMIT 20;
```

**A team's total 2025 salary cap commitment**
```sql
SELECT cb.team,
       COUNT(*) AS contracts_counted,
       SUM(cb.cap_number) AS total_cap_number,
       AVG(cb.cap_percent) AS avg_cap_pct
FROM contracts_cap_breakdown cb
WHERE cb.cap_year = '2025' AND cb.team = 'Cowboys';
```

**Biggest-guarantee deals ever signed (inflation-adjusted)**
```sql
SELECT c.year_signed, p.display_name, c.team, c.position,
       c.guaranteed, c.inflated_guaranteed
FROM contracts c
LEFT JOIN players p ON p.player_gsis_id = c.player_gsis_id
ORDER BY c.inflated_guaranteed DESC NULLS LAST LIMIT 25;
```

## Gotchas recap

- **APY is in millions** on `contracts`; **cap_number is in dollars** on `contracts_cap_breakdown`. Don't mix them.
- **`cap_percent` is a fraction** (0.032), not a percentage (3.2). Multiply by 100 for display.
- **Use `LEFT JOIN` when joining `contracts` → `players`** to preserve coach/retired-player rows.
- **`contracts.team` is the full name**, not the team abbreviation. Match on `LIKE 'Cowboys'`, not `'DAL'`.
- **Don't query `contracts.cols[1].cap_number`** — use `contracts_cap_breakdown` instead; it's the flat, queryable view of the same data.
- **`cap_year` is VARCHAR**. `WHERE cap_year = 2025` silently matches zero rows. Use `'2025'`.
