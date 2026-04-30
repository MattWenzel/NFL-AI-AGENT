import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Menu, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

interface AppShellProps {
  sidebar: ReactNode
  main: ReactNode
  inspector: ReactNode
  /** Drives auto-close (when false) and visibility of the right-edge open
   *  pill. Never auto-opens — user opens deliberately. */
  inspectorAvailable?: boolean
  /** Custom header label for the regular `inspector` slot (defaults to "Inspector"). */
  inspectorLabel?: string
  /** Identifier for the active surface (chat id, table id, "database"…).
   *  When this changes, the inspector force-closes so it never carries
   *  a stale view across pages. */
  surfaceKey?: string
}

type LayoutCtx = {
  /** Desktop conversation sidebar visibility. */
  desktopSidebarOpen: boolean
  toggleDesktopSidebar: () => void
  /** Open the desktop inspector pane (used when a tool row is clicked). */
  openDesktopInspector: () => void
  /** Close the desktop inspector (used by empty-thread clicks). */
  closeDesktopInspector: () => void
  /** Read-only inspector open state — for views that own a toggle button. */
  desktopInspectorOpen: boolean
  /** Flip the inspector's open state — used by the table-chat header toggle. */
  toggleDesktopInspector: () => void
  /** Dismiss the mobile sidebar sheet — wired into sidebar row clicks so
   *  picking an item doesn't leave the overlay covering the content. */
  closeMobileSidebar: () => void
}

const LayoutContext = createContext<LayoutCtx | null>(null)

export function useLayout(): LayoutCtx {
  return (
    useContext(LayoutContext) ?? {
      desktopSidebarOpen: true,
      toggleDesktopSidebar: () => {},
      openDesktopInspector: () => {},
      closeDesktopInspector: () => {},
      desktopInspectorOpen: false,
      toggleDesktopInspector: () => {},
      closeMobileSidebar: () => {},
    }
  )
}

export function AppShell({
  sidebar,
  main,
  inspector,
  inspectorAvailable = false,
  inspectorLabel,
  surfaceKey,
}: AppShellProps) {
  const activeInspectorLabel = inspectorLabel ?? 'Inspector'
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)
  const [desktopInspectorOpen, setDesktopInspectorOpen] = useState(false)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)
  // True at md+ (768px). Used to skip auto-opening sheets on mobile so a
  // navigation tap doesn't leave overlays covering the content.
  const [isDesktop, setIsDesktop] = useState<boolean>(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 768px)').matches,
  )
  useEffect(() => {
    if (typeof window === 'undefined') return
    const mq = window.matchMedia('(min-width: 768px)')
    const onChange = () => setIsDesktop(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Close mobile-only sheets when crossing into desktop so we don't carry
  // open state back if the viewport later shrinks again.
  useEffect(() => {
    if (isDesktop) {
      setMobileSidebarOpen(false)
      setMobileInspectorOpen(false)
    }
  }, [isDesktop])

  // Auto-close the desktop inspector when there's nothing worth inspecting,
  // but never auto-open — the user opens it deliberately via the right-edge
  // pill or by clicking a message.
  useEffect(() => {
    if (!inspectorAvailable) setDesktopInspectorOpen(false)
  }, [inspectorAvailable])

  // Force-close the inspector whenever the active surface changes so the
  // user never lands on a new page with the previous page's inspector view.
  useEffect(() => {
    setDesktopInspectorOpen(false)
    setMobileInspectorOpen(false)
  }, [surfaceKey])

  const inspectorRef = useRef<HTMLElement | null>(null)
  const mainRef = useRef<HTMLElement | null>(null)

  const ctx: LayoutCtx = {
    desktopSidebarOpen,
    toggleDesktopSidebar: () => setDesktopSidebarOpen((v) => !v),
    openDesktopInspector: () => setDesktopInspectorOpen(true),
    closeDesktopInspector: () => setDesktopInspectorOpen(false),
    desktopInspectorOpen,
    toggleDesktopInspector: () => setDesktopInspectorOpen((v) => !v),
    closeMobileSidebar: () => setMobileSidebarOpen(false),
  }

  return (
    <LayoutContext.Provider value={ctx}>
      <div className="relative flex h-dvh w-full overflow-hidden bg-background text-foreground">
        {!desktopSidebarOpen ? (
          <button
            type="button"
            onClick={() => setDesktopSidebarOpen(true)}
            className="absolute left-0 top-3 z-10 hidden h-8 w-8 items-center justify-center rounded-r-md border border-l-0 border-border bg-card text-muted-foreground transition-colors hover:bg-muted md:flex"
            aria-label="Open sidebar"
          >
            <PanelLeftOpen className="size-4" />
          </button>
        ) : null}

        {inspectorAvailable && !desktopInspectorOpen ? (
          <button
            type="button"
            onClick={() => setDesktopInspectorOpen(true)}
            className="absolute right-0 top-3 z-10 hidden h-8 w-8 items-center justify-center rounded-l-md border border-r-0 border-border bg-card text-muted-foreground transition-colors hover:bg-muted lg:flex"
            aria-label="Open inspector"
          >
            <PanelRightOpen className="size-4" />
          </button>
        ) : null}

        <aside
          className={cn(
            'hidden md:flex shrink-0 flex-col overflow-hidden border-r border-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out',
            desktopSidebarOpen ? 'w-[280px]' : 'w-0 border-r-0',
          )}
          aria-label="Conversations and reports"
          aria-hidden={!desktopSidebarOpen}
        >
          <div className="h-full w-[280px] shrink-0">{sidebar}</div>
        </aside>

        <Sheet open={!isDesktop && mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent
            side="left"
            className="data-[side=left]:w-[240px] data-[side=left]:sm:max-w-[240px] p-0 bg-sidebar text-sidebar-foreground"
          >
            {sidebar}
          </SheetContent>
        </Sheet>

        <div className="relative flex min-w-0 flex-1">
          <main ref={mainRef} className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-background/80 px-3 md:hidden">
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9"
                onClick={() => setMobileSidebarOpen(true)}
                aria-label="Open menu"
              >
                <Menu className="size-5" />
              </Button>
              <span className="font-display text-base font-semibold tracking-tight">NFL Stats</span>
              {inspectorAvailable ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-auto h-9 w-9"
                  onClick={() => setMobileInspectorOpen(true)}
                  aria-label="Open inspector"
                >
                  <PanelRightOpen className="size-5" />
                </Button>
              ) : null}
            </header>
            <div className="flex min-h-0 flex-1 flex-col">{main}</div>
          </main>

          <aside
            ref={inspectorRef}
            className={cn(
              'absolute inset-y-0 right-0 z-20 hidden lg:flex flex-col border-l border-border bg-card overflow-hidden shadow-xl transition-transform duration-200 ease-out',
              'w-[320px] xl:w-[380px] 2xl:w-[440px]',
              desktopInspectorOpen ? 'translate-x-0' : 'translate-x-full',
            )}
            aria-label="Runtime inspector"
            aria-hidden={!desktopInspectorOpen}
          >
            <div className="flex h-full flex-col overflow-hidden">
              <div className="flex h-12 items-center justify-between border-b border-border px-4">
                <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  {activeInspectorLabel}
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setDesktopInspectorOpen(false)}
                  aria-label={`Close ${activeInspectorLabel.toLowerCase()}`}
                >
                  <PanelRightClose className="size-4" />
                </Button>
              </div>
              <div className="flex min-h-0 flex-1 flex-col">{inspector}</div>
            </div>
          </aside>

          <Sheet open={!isDesktop && mobileInspectorOpen} onOpenChange={setMobileInspectorOpen}>
            <SheetContent
              side="right"
              className="flex w-[360px] flex-col p-0 bg-card"
            >
              <div className="flex h-12 shrink-0 items-center border-b border-border px-4">
                <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  {activeInspectorLabel}
                </span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col">{inspector}</div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </LayoutContext.Provider>
  )
}
