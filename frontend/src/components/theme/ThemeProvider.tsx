import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { ThemeContext, type ThemeMode } from '@/lib/theme'

const STORAGE_KEY = 'chat-workspace.theme'

function readStoredMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  const raw = window.localStorage.getItem(STORAGE_KEY)
  return raw === 'light' || raw === 'dark' || raw === 'cobalt' || raw === 'system' ? raw : 'system'
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode())
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark())

  useEffect(() => {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  const resolved: 'light' | 'dark' =
    mode === 'light'
      ? 'light'
      : mode === 'system'
        ? systemDark
          ? 'dark'
          : 'light'
        : 'dark'

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', resolved === 'dark')
    // System dark keeps the warm-charcoal default; explicit dark variants
    // layer their own palette on top of `.dark` via these toggle classes.
    root.classList.toggle('theme-midnight', mode === 'dark')
    root.classList.toggle('theme-cobalt', mode === 'cobalt')
    root.style.colorScheme = resolved
  }, [resolved, mode])

  const value = useMemo(
    () => ({
      mode,
      resolved,
      setMode: (next: ThemeMode) => {
        setModeState(next)
        try {
          window.localStorage.setItem(STORAGE_KEY, next)
        } catch {
          // ignore storage failures (private mode, etc.)
        }
      },
    }),
    [mode, resolved],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
