/**
 * `NormalizedMessage` — a render-friendly view of one chat message that's
 * agnostic to whichever store produced it. Both `chatStore` (transcript-shape:
 * turns + parts + tool_runs across three arrays) and `dbHelperChat`
 * (flat HelperMessage[]) expose adapters that emit `NormalizedMessage[]`,
 * so future shared rendering primitives can consume one schema instead
 * of three.
 *
 * Today's renderers (`Thread` / `HelperMessageList`) still read their
 * stores directly — this module is the seam, not yet the migration.
 */
import type { HelperMessage } from '@/lib/state/dbHelperChat'
import type {
  AssistantPartRecord,
  ConversationTranscript,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

export interface NormalizedToolRun {
  id: string
  name: string
  input: Record<string, unknown>
  status: 'pending' | 'completed' | 'error'
  /** The tool's stringified result body, when available. `null` while
   *  the call is still running, or for stores (the helper) that only
   *  track the wire string and didn't capture a parsed result. */
  result: string | null
  error: string | null
  durationMs: number | null
}

export interface NormalizedMessage {
  /** Stable id — turn id for the chat transcript, synthesized for the
   *  helper (it has no per-message ids). React-key-safe. */
  id: string
  role: 'user' | 'assistant'
  /** Concatenated visible text. Empty string when there's only a tool
   *  call and no narration yet. */
  text: string
  /** Tool runs the agent issued in this message. Empty for user messages. */
  toolRuns: NormalizedToolRun[]
  /** "Thinking" / reasoning text, when the provider exposed it as a
   *  separate part kind. Today only surfaces from the chat transcript. */
  thinking: string
  /** Lifecycle status of the message. The helper doesn't track per-
   *  message status, so it always reports 'complete' here. */
  status: 'streaming' | 'pending' | 'complete' | 'error' | 'interrupted'
  error: string | null
  provider: string | null
  model: string | null
  inputTokens: number
  outputTokens: number
  /** ISO timestamps. Both can be empty when the source store doesn't
   *  track creation/update times (the helper doesn't). */
  createdAt: string
  updatedAt: string
}

/** Adapt the chat-store transcript into normalized messages, one per
 *  visible turn. Compacted turns are filtered out (matching the
 *  thread renderer). Multi-iteration assistant turns are emitted
 *  separately — the renderer decides whether to group them. */
export function transcriptToNormalizedMessages(
  transcript: ConversationTranscript,
): NormalizedMessage[] {
  const partsByTurn = groupBy(transcript.parts, (p) => p.turn_id)
  const runsByTurn = groupBy(transcript.tool_runs, (r) => r.turn_id)

  const out: NormalizedMessage[] = []
  for (const turn of transcript.turns) {
    if (turn.compacted) continue
    if (turn.role === 'user') {
      out.push({
        id: turn.id,
        role: 'user',
        text: turn.text,
        toolRuns: [],
        thinking: '',
        status: turnStatus(turn),
        error: turn.error ?? null,
        provider: turn.provider ?? null,
        model: turn.model ?? null,
        inputTokens: turn.input_tokens || 0,
        outputTokens: turn.output_tokens || 0,
        createdAt: turn.created_at,
        updatedAt: turn.updated_at,
      })
      continue
    }
    const parts = (partsByTurn.get(turn.id) ?? [])
      .slice()
      .sort((a, b) => a.order_index - b.order_index)
    const text = joinPartContent(parts, ['text'])
    // AgentResponse accepts both 'thinking' and 'reasoning' depending
    // on which provider emitted the part — match that here so the
    // adapter doesn't drop content the live renderer surfaces.
    const thinking = joinPartContent(parts, ['thinking', 'reasoning'])
    const toolRuns = (runsByTurn.get(turn.id) ?? []).map(toolRunFromTranscript)
    out.push({
      id: turn.id,
      role: 'assistant',
      text,
      toolRuns,
      thinking,
      status: turnStatus(turn),
      error: turn.error ?? null,
      provider: turn.provider ?? null,
      model: turn.model ?? null,
      inputTokens: turn.input_tokens || 0,
      outputTokens: turn.output_tokens || 0,
      createdAt: turn.created_at,
      updatedAt: turn.updated_at,
    })
  }
  return out
}

/** Adapt the SQL helper's flat message list into normalized messages.
 *  Synthesizes stable ids from the index (the helper doesn't persist
 *  per-message ids — refresh wipes its state). */
export function helperMessagesToNormalized(
  messages: HelperMessage[],
): NormalizedMessage[] {
  return messages.map((m, i) => ({
    id: `helper-${i}`,
    role: m.role,
    text: m.text,
    toolRuns: (m.toolRuns ?? []).map((r) => ({
      id: r.id,
      name: r.name,
      input: r.input,
      status: r.status === 'failed' ? 'error' : r.status,
      result: r.content,
      error: r.error,
      durationMs: null,
    })),
    thinking: '',
    status: 'complete',
    error: null,
    provider: null,
    model: null,
    inputTokens: 0,
    outputTokens: 0,
    createdAt: '',
    updatedAt: '',
  }))
}

function toolRunFromTranscript(run: ToolRunRecord): NormalizedToolRun {
  return {
    id: run.id,
    name: run.tool_name,
    input: run.input,
    status: normalizeToolStatus(run.status),
    result: run.result,
    error: run.error,
    durationMs: run.duration_ms,
  }
}

/** Map any source status string to the lossy three-state vocabulary
 *  renderers need: pending / completed / error. The transcript uses
 *  'running'/'interrupted', the helper uses 'failed' — both fold in. */
function normalizeToolStatus(s: string): NormalizedToolRun['status'] {
  if (s === 'completed') return 'completed'
  if (s === 'error' || s === 'failed' || s === 'interrupted') return 'error'
  return 'pending'
}

function turnStatus(turn: TurnRecord): NormalizedMessage['status'] {
  // The transcript stores raw status strings. Normalize to the union.
  const s = turn.status
  if (s === 'streaming' || s === 'pending' || s === 'error' || s === 'interrupted') {
    return s
  }
  return 'complete'
}

function joinPartContent(parts: AssistantPartRecord[], kinds: string[]): string {
  let acc = ''
  for (const p of parts) {
    if (kinds.includes(p.kind) && typeof p.content === 'string') acc += p.content
  }
  return acc
}

function groupBy<T, K>(items: T[], key: (item: T) => K): Map<K, T[]> {
  const out = new Map<K, T[]>()
  for (const item of items) {
    const k = key(item)
    const list = out.get(k) ?? []
    list.push(item)
    out.set(k, list)
  }
  return out
}
