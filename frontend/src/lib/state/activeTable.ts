import { useCallback, useEffect, useState } from 'react'

import { apiGet, ApiError } from '@/lib/api'
import type { TableChatResponse, TableState } from '@/lib/api/tables'

/**
 * Live table state for the open report. Decoupled from the chat
 * transcript so the table can refresh independently when a `table_updated`
 * SSE event arrives without re-fetching the full transcript.
 */
export function useActiveTable(activeTableId: string | null) {
  const [table, setTable] = useState<TableState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    if (!activeTableId) {
      setTable(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await apiGet<TableChatResponse>(
        `/chat/tables/${encodeURIComponent(activeTableId)}`,
      )
      setTable(res.table)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not load report')
    } finally {
      setLoading(false)
    }
  }, [activeTableId])

  useEffect(() => {
    if (!activeTableId) {
      setTable(null)
      setError(null)
      return
    }
    refetch()
  }, [activeTableId, refetch])

  return { table, loading, error, refetch }
}
