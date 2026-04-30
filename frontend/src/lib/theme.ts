import { createContext, useContext } from 'react'

export type ThemeMode = 'light' | 'dark' | 'cobalt' | 'system'

export interface ThemeContextValue {
  mode: ThemeMode
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
  auroraEnabled: boolean
  setAuroraEnabled: (next: boolean) => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}
