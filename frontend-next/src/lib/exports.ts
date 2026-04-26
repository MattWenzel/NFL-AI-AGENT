/**
 * CSV library wire types — mirror backend/api/schemas/exports.py.
 */

export interface ExportInfo {
  id: string
  filename: string
  title: string
  row_count: number
  columns: string[]
  file_size: number
  created_at: string
  updated_at: string
  download_url: string
  source_session_id: string | null
}

export interface ExportDetail extends ExportInfo {
  sql: string
  preview_rows: Record<string, unknown>[]
  preview_truncated: boolean
}

export interface NewSessionFromExportResponse {
  conversation_id: string
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}
