/**
 * Wire types mirroring backend/api/schemas/conversations.py and the SSE
 * payloads from backend/server/sse.py. Keep these in sync with the backend.
 */

export type TurnRole = 'user' | 'assistant'
export type TurnStatus = 'pending' | 'streaming' | 'complete' | 'error' | 'interrupted'

export interface TurnRecord {
  id: string
  role: TurnRole
  status: TurnStatus | string
  text: string
  compacted: boolean
  error: string | null
  input_tokens: number
  output_tokens: number
  created_at: string
  updated_at: string
}

export type AssistantPartKind = 'text' | 'tool_use' | 'thinking' | string

export interface AssistantPartRecord {
  id: string
  turn_id: string
  kind: AssistantPartKind
  order_index: number
  content: string
  name?: string | null
  tool_run_id?: string | null
  created_at: string
}

export type ToolRunStatus = 'pending' | 'running' | 'completed' | 'error' | 'interrupted' | string

export interface ToolRunRecord {
  id: string
  turn_id: string
  tool_name: string
  input: Record<string, unknown>
  status: ToolRunStatus
  result: string | null
  error: string | null
  hint: string | null
  duration_ms: number | null
  compacted: boolean
  created_at: string
  updated_at: string
}

export interface CompactionSummaryRecord {
  id: string
  summary_turn_id: string
  source_turn_ids: string[]
  created_at: string
}

export interface ConversationTranscript {
  session_id: string
  title: string | null
  provider: string | null
  model: string | null
  updated_at: string | null
  turns: TurnRecord[]
  parts: AssistantPartRecord[]
  tool_runs: ToolRunRecord[]
  summaries: CompactionSummaryRecord[]
}

export interface ConversationInfo {
  id: string
  message_count: number
  title: string
  provider: string | null
  model: string | null
  updated_at: string | null
  pinned_at: string | null
  source_csv_id: string | null
}
