import { createContext, useContext, type ReactNode } from 'react'

import { useExports } from '@/lib/exportsStore'

type ExportsContextValue = ReturnType<typeof useExports>

const ExportsContext = createContext<ExportsContextValue | null>(null)

export function ExportsProvider({ children }: { children: ReactNode }) {
  const value = useExports()
  return <ExportsContext.Provider value={value}>{children}</ExportsContext.Provider>
}

export function useExportsContext() {
  const ctx = useContext(ExportsContext)
  if (!ctx) throw new Error('useExportsContext must be used inside <ExportsProvider>')
  return ctx
}
