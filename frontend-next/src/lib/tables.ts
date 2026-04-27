/**
 * Table-view chat wire types — mirror backend/api/schemas/tables.py.
 */

import type { ConversationTranscript } from '@/lib/types'

export interface TableState {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  last_sql: string | null
  updated_at: string
}

export interface TableChatResponse {
  conversation: ConversationTranscript
  table: TableState | null
}

/** Allowed table-size dropdown values. `'auto'` lets the agent pick the
 *  row count itself (capped at the sandbox's absolute 500 ceiling). */
export const TABLE_SIZE_OPTIONS = ['auto', 25, 50, 100, 250, 500] as const
export type TableSize = (typeof TABLE_SIZE_OPTIONS)[number]
export const DEFAULT_TABLE_SIZE: TableSize = 'auto'

/** Allowed mode dropdown values. */
export type TableMode = 'explore' | 'edit_table'
export const DEFAULT_TABLE_MODE: TableMode = 'explore'
