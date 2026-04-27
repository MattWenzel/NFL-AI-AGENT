import { createContext, useContext, type ReactNode } from 'react'

import { useTableChats } from '@/lib/tablesStore'

type TablesContextValue = ReturnType<typeof useTableChats>

const TablesContext = createContext<TablesContextValue | null>(null)

export function TablesProvider({ children }: { children: ReactNode }) {
  const value = useTableChats()
  return <TablesContext.Provider value={value}>{children}</TablesContext.Provider>
}

export function useTablesContext() {
  const ctx = useContext(TablesContext)
  if (!ctx) throw new Error('useTablesContext must be used inside <TablesProvider>')
  return ctx
}
