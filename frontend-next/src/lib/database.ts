/**
 * Database browser wire types — mirror backend/api/schemas/database.py.
 */

import { apiFetch, apiGet } from '@/lib/api'

export interface DatabaseTableColumn {
  name: string
  type: string
}

export interface DatabaseTableInfo {
  name: string
  columns: DatabaseTableColumn[]
}

export interface DatabaseQueryResult {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
}

export interface SaveAsReportPayload {
  sql: string
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  title?: string | null
}

export async function fetchDatabaseTables(): Promise<DatabaseTableInfo[]> {
  return apiGet<DatabaseTableInfo[]>('/database/tables')
}

export async function runDatabaseQuery(sql: string): Promise<DatabaseQueryResult> {
  const res = await apiFetch('/database/query', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
  return (await res.json()) as DatabaseQueryResult
}

export async function saveQueryAsReport(
  payload: SaveAsReportPayload,
): Promise<{ conversation_id: string }> {
  const res = await apiFetch('/database/save-as-report', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return (await res.json()) as { conversation_id: string }
}
