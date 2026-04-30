import { useCallback, useEffect, useState } from 'react'

import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from '@/lib/api'
import type { ConversationInfo } from '@/lib/types'

interface TableChatCreate {
  title?: string
  provider?: string
  model?: string
}

export function useTableChats() {
  const [tables, setTables] = useState<ConversationInfo[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setStatus('loading')
    try {
      const list = await apiGet<ConversationInfo[]>('/chat/tables')
      setTables(list)
      setStatus('ready')
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not load tables')
      setStatus('error')
    }
  }, [])

  const create = useCallback(
    async (body: TableChatCreate = {}) => {
      const created = await apiPost<ConversationInfo>('/chat/tables', body)
      await refresh()
      return created
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: string) => {
      try {
        await apiDelete(`/chat/tables/${encodeURIComponent(id)}`)
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
      await apiPatch<ConversationInfo>(`/chat/tables/${encodeURIComponent(id)}`, {
        title: trimmed,
      })
      await refresh()
    },
    [refresh],
  )

  const setPinned = useCallback(
    async (id: string, pinned: boolean) => {
      await apiPatch<ConversationInfo>(`/chat/tables/${encodeURIComponent(id)}`, {
        pinned,
      })
      await refresh()
    },
    [refresh],
  )

  useEffect(() => {
    refresh()
  }, [refresh])

  return { tables, status, error, refresh, create, remove, rename, setPinned }
}
