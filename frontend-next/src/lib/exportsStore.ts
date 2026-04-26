import { useCallback, useEffect, useState } from 'react'

import { apiDelete, apiGet, ApiError } from '@/lib/api'
import type { ExportInfo } from '@/lib/exports'

export function useExports() {
  const [exports, setExports] = useState<ExportInfo[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setStatus('loading')
    try {
      const list = await apiGet<ExportInfo[]>('/chat/exports')
      setExports(list)
      setStatus('ready')
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not load exports')
      setStatus('error')
    }
  }, [])

  const remove = useCallback(
    async (id: string) => {
      try {
        await apiDelete(`/chat/exports/${encodeURIComponent(id)}`)
      } catch {
        // ignore — list refresh will surface
      }
      await refresh()
    },
    [refresh],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  return { exports, status, error, refresh, remove }
}
