import type {
  AssistantPartRecord,
  ConversationTranscript,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

/** A user/assistant exchange — the user turn that triggered it plus the
 *  one-or-more assistant turns that followed (multi-iteration tool runs
 *  produce several assistant turns under one user turn). Used by the
 *  inspector's exchange view and the ToolRunInspector's prev/next nav. */
export interface ExchangeSlice {
  userTurn: TurnRecord | null
  assistantTurns: TurnRecord[]
  parts: AssistantPartRecord[]
  toolRuns: ToolRunRecord[]
}

/** Tools whose primary input is a `sql` string and warrant the
 *  ToolRunDetail SQL viewer. Kept as a single source of truth so the
 *  inspector, the thread tool-row click, and the SQL summarizer all
 *  agree. */
export function hasSqlPayload(toolName: string): boolean {
  return (
    toolName === 'execute_sql' ||
    toolName === 'run_sql' ||
    toolName === 'set_table'
  )
}

/** Walk back/forward from `exchangeId` to assemble the user→assistant
 *  block it anchors. `exchangeId` is normally the user turn's id but
 *  also accepts an assistant turn id (we walk back to its triggering
 *  user turn). */
export function sliceForExchange(
  transcript: ConversationTranscript,
  exchangeId: string,
): ExchangeSlice | null {
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)
  const idx = visibleTurns.findIndex((t) => t.id === exchangeId)
  if (idx < 0) return null
  const anchor = visibleTurns[idx]

  let userTurn: TurnRecord | null = null
  let firstAgentIdx: number
  if (anchor.role === 'user') {
    userTurn = anchor
    firstAgentIdx = idx + 1
  } else {
    firstAgentIdx = idx
    for (let i = idx - 1; i >= 0; i--) {
      if (visibleTurns[i].role === 'user') {
        userTurn = visibleTurns[i]
        break
      }
      firstAgentIdx = i
    }
  }

  const assistantTurns: TurnRecord[] = []
  for (let i = firstAgentIdx; i < visibleTurns.length; i++) {
    const t = visibleTurns[i]
    if (t.role === 'user') break
    assistantTurns.push(t)
  }

  const turnIds = new Set(assistantTurns.map((t) => t.id))
  const parts = transcript.parts.filter((p) => turnIds.has(p.turn_id))
  const toolRuns = transcript.tool_runs.filter((r) => turnIds.has(r.turn_id))
  return { userTurn, assistantTurns, parts, toolRuns }
}

/** Resolve the exchange anchor for a turn — the turn itself when it's a
 *  user turn, or the most recent preceding user turn when it's an
 *  assistant turn. Returns null if no anchor exists. */
export function exchangeIdForTurn(
  transcript: ConversationTranscript,
  turnId: string,
): string | null {
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)
  const idx = visibleTurns.findIndex((t) => t.id === turnId)
  if (idx < 0) return null
  if (visibleTurns[idx].role === 'user') return visibleTurns[idx].id
  for (let i = idx - 1; i >= 0; i--) {
    if (visibleTurns[i].role === 'user') return visibleTurns[i].id
  }
  return null
}

/** Render the `input` field of a tool run for display. SQL-bearing
 *  tools render the query verbatim — JSON-encoding it just buries the
 *  SQL in escaped quotes and \n. */
export function inputDisplayValue(run: ToolRunRecord): string {
  if (hasSqlPayload(run.tool_name)) {
    const sql = (run.input as { sql?: unknown }).sql
    if (typeof sql === 'string') return sql
  }
  return JSON.stringify(run.input, null, 2)
}
