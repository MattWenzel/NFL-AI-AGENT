import type { ConversationTranscript } from '@/lib/types'

/**
 * Hand-written mock transcript for building the thread UI without SSE wiring.
 * Mirrors the backend's projection shape so the same components render the
 * real transcript later.
 */
export const MOCK_TRANSCRIPT: ConversationTranscript = {
  session_id: 'mock-session-1',
  title: 'Saquon 2024 vs Barry Sanders best year',
  provider: 'anthropic',
  model: 'claude-sonnet-4-6',
  updated_at: '2026-04-26T10:00:00Z',
  turns: [
    {
      id: 'turn-user-1',
      role: 'user',
      status: 'complete',
      text:
        "How does Saquon Barkley's 2024 RB1 season compare to Barry Sanders' best year? Show rushing yards, YPC, TDs, and total scrimmage yards side-by-side.",
      compacted: false,
      error: null,
      input_tokens: 0,
      output_tokens: 0,
      provider: null,
      model: null,
      created_at: '2026-04-26T10:00:00Z',
      updated_at: '2026-04-26T10:00:00Z',
    },
    {
      id: 'turn-assistant-1',
      role: 'assistant',
      status: 'complete',
      text: '',
      compacted: false,
      error: null,
      input_tokens: 1240,
      output_tokens: 856,
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      created_at: '2026-04-26T10:00:01Z',
      updated_at: '2026-04-26T10:00:09Z',
    },
  ],
  parts: [
    {
      id: 'part-1',
      turn_id: 'turn-assistant-1',
      kind: 'text',
      order_index: 0,
      content:
        "I'll pull Saquon's 2024 season stats and Barry Sanders' best statistical year — by total yards, that's 1997.\n",
      created_at: '2026-04-26T10:00:01Z',
    },
    {
      id: 'part-2',
      turn_id: 'turn-assistant-1',
      kind: 'tool_use',
      order_index: 1,
      content: '',
      name: 'run_sql',
      tool_run_id: 'tool-1',
      created_at: '2026-04-26T10:00:02Z',
    },
    {
      id: 'part-3',
      turn_id: 'turn-assistant-1',
      kind: 'tool_use',
      order_index: 2,
      content: '',
      name: 'run_sql',
      tool_run_id: 'tool-2',
      created_at: '2026-04-26T10:00:05Z',
    },
    {
      id: 'part-4',
      turn_id: 'turn-assistant-1',
      kind: 'text',
      order_index: 3,
      content: `Side by side, it's closer than it gets credit for:

| Metric | Saquon Barkley (2024) | Barry Sanders (1997) |
| --- | ---: | ---: |
| Rushing yards | 2,005 | 2,053 |
| Carries | 378 | 335 |
| YPC | 5.30 | 6.13 |
| Rushing TDs | 13 | 11 |
| Receiving yards | 278 | 305 |
| Total scrimmage yards | 2,283 | 2,358 |

**Key differences:**

- **Sanders was more efficient** — 0.83 more yards per carry on 43 fewer touches. He also broke off more long runs (53 carries of 10+ yards vs Saquon's 41).
- **Saquon scored more on the ground** — 13 rushing TDs to 11. Sanders' Lions led the league in red-zone *pass* attempts that year, which compressed his goal-line work.
- **Receiving roles diverged** — Saquon caught fewer balls (33 vs 33) for similar yardage despite playing in a more pass-heavy league. Sanders was a steady checkdown option in Detroit's offense; Saquon was a true RB1 who Hurts threw to less than a typical PPR back.
- **Era context** — Saquon's 5.30 YPC against modern defenses (5+ DBs as the base nearly every snap) is arguably more impressive in scheme-adjusted terms; Sanders ran behind a top-3 OL against base 4-3 fronts.

Want me to pull DVOA / EPA-per-play to make the era comparison less hand-wavey?`,
      created_at: '2026-04-26T10:00:08Z',
    },
  ],
  tool_runs: [
    {
      id: 'tool-1',
      turn_id: 'turn-assistant-1',
      tool_name: 'run_sql',
      input: {
        sql:
          "SELECT player_display_name, season, rushing_yards, carries, rushing_tds, " +
          "receiving_yards, receptions, rushing_yards / NULLIF(carries, 0) AS ypc " +
          "FROM season_stats WHERE player_gsis_id = '00-0034844' AND season = 2024 AND season_type = 'REG'",
      },
      status: 'completed',
      result: JSON.stringify(
        [
          {
            player_display_name: 'Saquon Barkley',
            season: 2024,
            rushing_yards: 2005,
            carries: 378,
            rushing_tds: 13,
            receiving_yards: 278,
            receptions: 33,
            ypc: 5.30,
          },
        ],
        null,
        2,
      ),
      error: null,
      hint: null,
      duration_ms: 47,
      compacted: false,
      created_at: '2026-04-26T10:00:02Z',
      updated_at: '2026-04-26T10:00:02Z',
    },
    {
      id: 'tool-2',
      turn_id: 'turn-assistant-1',
      tool_name: 'run_sql',
      input: {
        sql:
          "SELECT player_display_name, season, rushing_yards, carries, rushing_tds, " +
          "receiving_yards, receptions, rushing_yards / NULLIF(carries, 0) AS ypc " +
          "FROM season_stats WHERE player_pfr_id = 'SandBa00' AND season_type = 'REG' " +
          "ORDER BY rushing_yards + receiving_yards DESC LIMIT 1",
      },
      status: 'completed',
      result: JSON.stringify(
        [
          {
            player_display_name: 'Barry Sanders',
            season: 1997,
            rushing_yards: 2053,
            carries: 335,
            rushing_tds: 11,
            receiving_yards: 305,
            receptions: 33,
            ypc: 6.13,
          },
        ],
        null,
        2,
      ),
      error: null,
      hint: null,
      duration_ms: 39,
      compacted: false,
      created_at: '2026-04-26T10:00:05Z',
      updated_at: '2026-04-26T10:00:05Z',
    },
  ],
  summaries: [],
}
