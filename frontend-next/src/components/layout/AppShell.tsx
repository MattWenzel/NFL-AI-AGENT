import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Menu, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

interface AppShellProps {
  sidebar: ReactNode
  main: ReactNode
  inspector: ReactNode
  /** Inspector defaults open only when the parent has something worth showing. */
  inspectorAvailable?: boolean
}

type LayoutCtx = {
  /** Desktop conversation sidebar visibility. */
  desktopSidebarOpen: boolean
  toggleDesktopSidebar: () => void
  /** Open the desktop inspector pane (used when a tool row is clicked). */
  openDesktopInspector: () => void
  /** Close the desktop inspector (used by empty-thread clicks). */
  closeDesktopInspector: () => void
}

const LayoutContext = createContext<LayoutCtx | null>(null)

export function useLayout(): LayoutCtx {
  return (
    useContext(LayoutContext) ?? {
      desktopSidebarOpen: true,
      toggleDesktopSidebar: () => {},
      openDesktopInspector: () => {},
      closeDesktopInspector: () => {},
    }
  )
}

export function AppShell({ sidebar, main, inspector, inspectorAvailable = false }: AppShellProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)
  const [desktopInspectorOpen, setDesktopInspectorOpen] = useState(false)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)

  // Auto-open the desktop inspector once a transcript is loaded; force-close
  // when there's nothing to inspect (e.g. report viewer) so the floating
  // button can't overlap unrelated UI.
  useEffect(() => {
    setDesktopInspectorOpen(inspectorAvailable)
  }, [inspectorAvailable])

  // Close the desktop inspector when a pointer-down lands outside of it.
  // Clicks inside <main> (the thread + composer) are NOT treated as outside —
  // selecting another message would otherwise race close-then-open, flicker
  // the inspector closed mid-click, and reflow the page in a way that can
  // swallow the click event entirely.
  const inspectorRef = useRef<HTMLElement | null>(null)
  const mainRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!desktopInspectorOpen) return
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target
      if (!(target instanceof Node)) return
      if (inspectorRef.current?.contains(target)) return
      if (mainRef.current?.contains(target)) return
      if (!(target instanceof Element)) return
      // Radix popovers (Select, DropdownMenu, etc.) portal their content
      // to <body>, so a click on a Select item lives outside <main>.
      // Popper-positioned content (DropdownMenu, default Popover) wraps
      // in [data-radix-popper-content-wrapper]; item-aligned Select
      // content does not, so also match the shadcn portal data-slots.
      if (
        target.closest(
          '[data-radix-popper-content-wrapper],' +
            '[data-slot="select-content"],' +
            '[data-slot="dropdown-menu-content"],' +
            '[data-slot="popover-content"],' +
            '[data-slot="dialog-content"],' +
            '[data-slot="alert-dialog-content"]',
        )
      ) {
        return
      }
      // Radix modal Select sets pointer-events: none on outside content
      // while open, which deflects the click target up to <html>/<body>.
      // For one or two events after the dropdown closes that deflection
      // can still leak; treat body/html as ambiguous and don't close.
      if (target === document.documentElement || target === document.body) return
      setDesktopInspectorOpen(false)
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [desktopInspectorOpen])

  const ctx: LayoutCtx = {
    desktopSidebarOpen,
    toggleDesktopSidebar: () => setDesktopSidebarOpen((v) => !v),
    openDesktopInspector: () => setDesktopInspectorOpen(true),
    closeDesktopInspector: () => setDesktopInspectorOpen(false),
  }

  return (
    <LayoutContext.Provider value={ctx}>
      <div className="flex h-dvh w-full overflow-hidden bg-background text-foreground">
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

        <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent side="left" className="w-[300px] p-0 bg-sidebar text-sidebar-foreground">
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
              <Button
                variant="ghost"
                size="icon"
                className="ml-auto h-9 w-9"
                onClick={() => setMobileInspectorOpen(true)}
                aria-label="Open inspector"
              >
                <PanelRightOpen className="size-5" />
              </Button>
            </header>
            <div className="flex min-h-0 flex-1 flex-col">{main}</div>
          </main>

          {!desktopSidebarOpen ? (
            <button
              type="button"
              onClick={() => setDesktopSidebarOpen(true)}
              className="absolute left-0 top-3 hidden h-8 w-8 items-center justify-center rounded-r-md border border-l-0 border-border bg-card text-muted-foreground transition-colors hover:bg-muted md:flex"
              aria-label="Open sidebar"
            >
              <PanelLeftOpen className="size-4" />
            </button>
          ) : null}

          <aside
            ref={inspectorRef}
            className={cn(
              'hidden lg:flex shrink-0 flex-col border-l border-border bg-card transition-[width] duration-200 ease-out',
              desktopInspectorOpen ? 'w-[360px]' : 'w-0',
            )}
            aria-label="Runtime inspector"
            aria-hidden={!desktopInspectorOpen}
          >
            {desktopInspectorOpen ? (
              <div className="flex h-full flex-col overflow-hidden">
                <div className="flex h-12 items-center justify-between border-b border-border px-4">
                  <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                    Inspector
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setDesktopInspectorOpen(false)}
                    aria-label="Close inspector"
                  >
                    <PanelRightClose className="size-4" />
                  </Button>
                </div>
                <div className="flex min-h-0 flex-1 flex-col">{inspector}</div>
              </div>
            ) : null}
          </aside>

          {!desktopInspectorOpen && inspectorAvailable ? (
            <button
              type="button"
              onClick={() => setDesktopInspectorOpen(true)}
              className="absolute right-3 top-3 hidden items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-muted-foreground shadow-sm transition-colors hover:bg-muted hover:text-foreground lg:flex"
            >
              <PanelRightOpen className="size-3.5" />
              <span>Inspector</span>
            </button>
          ) : null}

          <Sheet open={mobileInspectorOpen} onOpenChange={setMobileInspectorOpen}>
            <SheetContent side="right" className="w-[360px] p-0 bg-card lg:hidden">
              <div className="flex h-12 items-center border-b border-border px-4">
                <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Inspector
                </span>
              </div>
              <div className="overflow-y-auto p-4">{inspector}</div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </LayoutContext.Provider>
  )
}
