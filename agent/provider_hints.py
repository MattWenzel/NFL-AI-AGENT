"""Per-provider supplemental hints appended to the shared system prompt.

Some models need extra operational reminders that Claude follows reliably.
Rather than bloating the shared prompt, we inject them only where needed.
"""

PROVIDER_HINTS: dict[str, str] = {
    "openai": """\
## Provider-Specific Reminders (OpenAI)

1. **Always call `get_schema` before writing SQL** for these tables: `ngs_stats`, \
`pfr_advanced`, `combine`, `qbr`. Do NOT guess column names — they differ from \
game_stats/season_stats naming.

2. **NGS receiving columns** — the correct names are:
   - `targets` (NOT `attempts` — that column is NULL for receiving rows)
   - `receptions`, `yards`, `rec_touchdowns`
   - `avg_separation`, `avg_cushion`, `avg_intended_air_yards`
   Do NOT assume game_stats column names carry over to ngs_stats.
   **IMPORTANT**: When users say "attempts" in a receiving context (WR/TE route stats, \
separation, cushion), they mean `targets`. The `attempts` column exists in ngs_stats \
but is only populated for passing/rushing rows — it is NULL for receiving. Always \
translate "attempts" → `targets` for receiving queries.

3. **Multi-season aggregation** — When the user says "over the past N years" or \
"with a minimum of X targets/carries/etc.", aggregate across seasons:
   ```sql
   GROUP BY player columns
   HAVING SUM(targets) >= 200   -- threshold applies to the TOTAL, not per-row
   ```
   Do NOT use `WHERE targets >= 200` — that filters individual rows, not career totals.

4. **When a query returns 0 rows** — do NOT immediately conclude "no data exists." \
Re-examine your column names and filters. Call `get_schema` for the table and retry \
with corrected column names. Zero rows almost always means a wrong column name or \
filter value, not missing data.

5. **ngs_stats uses different column names from game_stats** — `player_gsis_id` \
(NOT `gsis_id`), `player_display_name` (NOT `display_name`), `team_abbr` (NOT \
`team`), `pass_yards` (NOT `passing_yards`), `pass_touchdowns` (NOT `passing_tds`). \
Always call get_schema first for ngs_stats queries.""",
}


def get_system_prompt(provider: str) -> str:
    """Return system prompt (with fresh date) plus any per-provider hints."""
    from agent.system_prompt import get_base_prompt

    prompt = get_base_prompt()
    hints = PROVIDER_HINTS.get(provider, "")
    if hints:
        return prompt + "\n\n" + hints
    return prompt
