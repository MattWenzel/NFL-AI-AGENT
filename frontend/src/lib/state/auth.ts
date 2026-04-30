import { useCallback, useEffect, useState } from 'react'

import { apiGet, apiPost, ApiError } from '@/lib/api'

export interface AuthUser {
  id: number
  email: string
  role: string
  email_verified: boolean
}

interface AuthStatus {
  authenticated: boolean
  user: AuthUser | null
}

export type AuthState =
  | { status: 'loading' }
  | { status: 'anonymous'; error?: string }
  | { status: 'authenticated'; user: AuthUser }

export function useAuth() {
  const [state, setState] = useState<AuthState>({ status: 'loading' })

  const refresh = useCallback(async () => {
    try {
      const status = await apiGet<AuthStatus>('/auth/status')
      if (status.authenticated && status.user) {
        setState({ status: 'authenticated', user: status.user })
      } else {
        setState({ status: 'anonymous' })
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setState({ status: 'anonymous' })
      } else {
        setState({ status: 'anonymous', error: 'Could not reach the server.' })
      }
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const login = useCallback(
    async (email: string, password: string) => {
      try {
        await apiPost('/auth/login', { email, password })
        await refresh()
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : 'Login failed'
        setState({ status: 'anonymous', error: msg })
        throw e
      }
    },
    [refresh],
  )

  const register = useCallback(
    async (email: string, password: string, inviteCode?: string) => {
      try {
        await apiPost('/auth/register', {
          email,
          password,
          invite_code: inviteCode || undefined,
        })
        await refresh()
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : 'Sign up failed'
        setState({ status: 'anonymous', error: msg })
        throw e
      }
    },
    [refresh],
  )

  const logout = useCallback(async () => {
    try {
      await apiPost('/auth/logout', {})
    } catch {
      // ignore — even if server-side logout fails, drop local state.
    }
    setState({ status: 'anonymous' })
  }, [])

  return { state, refresh, login, register, logout }
}
