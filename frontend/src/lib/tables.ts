/**
 * Table-view chat wire types — mirror backend/api/schemas/tables.py.
 */

import { apiFetch } from '@/lib/api'
import type { ConversationTranscript } from '@/lib/types'

export interface TableState {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  locked: boolean
  last_sql: string | null
  updated_at: string
}

export interface TableChatResponse {
  conversation: ConversationTranscript
  table: TableState | null
}

export async function setTableLocked(
  conversationId: string,
  locked: boolean,
): Promise<void> {
  await apiFetch(`/chat/tables/${encodeURIComponent(conversationId)}/lock`, {
    method: 'PUT',
    body: JSON.stringify({ locked }),
  })
}
