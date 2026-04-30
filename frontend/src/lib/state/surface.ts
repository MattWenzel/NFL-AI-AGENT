import { useCallback, useEffect, useState } from 'react'

/** The set of in-app surfaces the URL can identify. Drives App.tsx's
 *  rendering; every navigation pushes/replaces a history entry so
 *  the browser's back/forward buttons traverse this set. */
export type Surface =
  | { kind: 'home' }
  | { kind: 'chat'; id: string }
  | { kind: 'report'; id: string }
  | { kind: 'pending-report' }
  | { kind: 'database'; table?: string }

const HOME: Surface = { kind: 'home' }

export function surfaceToPath(s: Surface): string {
  switch (s.kind) {
    case 'home':
      return '/'
    case 'chat':
      return `/chat/${encodeURIComponent(s.id)}`
    case 'report':
      return `/report/${encodeURIComponent(s.id)}`
    case 'pending-report':
      return '/report/new'
    case 'database':
      return s.table
        ? `/database/${encodeURIComponent(s.table)}`
        : '/database'
  }
}

export function parseSurface(pathname: string): Surface {
  const parts = pathname.replace(/^\/+/, '').replace(/\/+$/, '').split('/')
  if (parts.length === 0 || parts[0] === '') return HOME
  if (parts[0] === 'chat' && parts[1]) {
    return { kind: 'chat', id: decodeURIComponent(parts[1]) }
  }
  if (parts[0] === 'report') {
    if (parts[1] === 'new') return { kind: 'pending-report' }
    if (parts[1]) return { kind: 'report', id: decodeURIComponent(parts[1]) }
  }
  if (parts[0] === 'database') {
    return {
      kind: 'database',
      table: parts[1] ? decodeURIComponent(parts[1]) : undefined,
    }
  }
  // Anything else (typos, dead links) falls back to home so the SPA
  // doesn't render in a broken state.
  return HOME
}

export function surfacesEqual(a: Surface, b: Surface): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'chat' && b.kind === 'chat') return a.id === b.id
  if (a.kind === 'report' && b.kind === 'report') return a.id === b.id
  if (a.kind === 'database' && b.kind === 'database') return (a.table ?? null) === (b.table ?? null)
  return true
}

interface UseSurfaceResult {
  surface: Surface
  /** Push or replace a history entry and update local state. Use
   *  `replace: true` for transitions where back-button-returning to the
   *  pre-replace state would feel wrong (e.g. promoting a fresh chat's
   *  URL from `/` to `/chat/:id` once the conversation_id materializes). */
  navigate: (next: Surface, options?: { replace?: boolean }) => void
}

/** URL-driven app navigation. Returns the Surface parsed from the
 *  current path, listens for browser back/forward via `popstate`, and
 *  exposes a `navigate` setter that updates both `history` and React
 *  state in lockstep. */
export function useSurface(): UseSurfaceResult {
  const [surface, setSurface] = useState<Surface>(() =>
    typeof window === 'undefined' ? HOME : parseSurface(window.location.pathname),
  )

  useEffect(() => {
    const onPop = () => setSurface(parseSurface(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback(
    (next: Surface, options?: { replace?: boolean }) => {
      const path = surfaceToPath(next)
      if (typeof window !== 'undefined' && window.location.pathname !== path) {
        if (options?.replace) {
          window.history.replaceState(null, '', path)
        } else {
          window.history.pushState(null, '', path)
        }
      }
      setSurface(next)
    },
    [],
  )

  return { surface, navigate }
}
