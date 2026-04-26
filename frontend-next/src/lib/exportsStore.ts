import { useCallback, useEffect, useState } from 'react'

import { apiDelete, apiGet, apiPatch, ApiError } from '@/lib/api'
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

  const rename = useCallback(
    async (id: string, title: string) => {
      const trimmed = title.trim()
      if (!trimmed) return
      await apiPatch<ExportInfo>(`/chat/exports/${encodeURIComponent(id)}`, {
        title: trimmed,
      })
      await refresh()
    },
    [refresh],
  )

  const setPinned = useCallback(
    async (id: string, pinned: boolean) => {
      await apiPatch<ExportInfo>(`/chat/exports/${encodeURIComponent(id)}`, {
        pinned,
      })
      await refresh()
    },
    [refresh],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  return { exports, status, error, refresh, remove, rename, setPinned }
}
