import { createContext, useContext, type ReactNode } from 'react'

interface NavigationCtx {
  /** Open the Report identified by the given table_chat session id —
   *  same surface-switch the sidebar's report rows trigger. */
  openReport: (id: string) => void
}

const NavigationContext = createContext<NavigationCtx | null>(null)

/** Tiny app-level navigation context. Avoids prop-drilling `openTable`
 *  through the chat tree just so an in-thread component (e.g. the
 *  report-created link card in `AgentResponse`) can switch surfaces. */
export function NavigationProvider({
  openReport,
  children,
}: NavigationCtx & { children: ReactNode }) {
  return (
    <NavigationContext.Provider value={{ openReport }}>
      {children}
    </NavigationContext.Provider>
  )
}

export function useNavigation(): NavigationCtx {
  const ctx = useContext(NavigationContext)
  if (!ctx) {
    // Safe default for stories / tests that mount components without
    // the provider — no-op so click handlers don't blow up.
    return { openReport: () => {} }
  }
  return ctx
}
