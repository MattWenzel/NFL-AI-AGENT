/**
 * Provider registry — server-driven, mirrors GET /chat/providers.
 *
 * The backend is the source of truth for which providers exist, which models
 * each supports, and which default model to use. The frontend just renders
 * what it gets so things like Codex's `gpt-5.3-codex`-only model list don't
 * drift out of sync with reality.
 */

import { useEffect, useState } from 'react'

import { apiGet, ApiError } from '@/lib/api'

export interface ProviderInfo {
  name: string
  display_name: string
  models: string[]
  default_model: string
  available: boolean
  context_window: number
  supports_streaming: boolean
  supports_tools: boolean
}

export type ProvidersStatus = 'idle' | 'loading' | 'ready' | 'error'

export function useProviders(): {
  providers: ProviderInfo[]
  status: ProvidersStatus
  error: string | null
} {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [status, setStatus] = useState<ProvidersStatus>('loading')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    apiGet<ProviderInfo[]>('/chat/providers')
      .then((list) => {
        if (cancelled) return
        setProviders(list)
        setStatus('ready')
        setError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setStatus('error')
        setError(e instanceof ApiError ? e.detail : 'Could not load providers')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { providers, status, error }
}
