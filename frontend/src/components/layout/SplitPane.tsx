import { useEffect, useRef, useState, type ReactNode } from 'react'
import { GripHorizontal } from 'lucide-react'

import { cn } from '@/lib/utils'

/** Vertical-split layout state. Both Reports (TableChatView) and Database
 *  expose maximize/minimize buttons that flip between these modes —
 *  semantically: top = table, bottom = chat. */
export type SplitMode = 'split' | 'top-min' | 'top-max' | 'bottom-max'

interface SplitPaneProps {
  /** Controlled split mode. The consumer owns this so it can flip back
   *  to `split` on side-effects (e.g. when the user sends a message
   *  while in `top-max`, so the agent's reply isn't hidden). */
  mode: SplitMode
  /** Slot for the top half. Wrap your existing card content as-is —
   *  `SplitPane` adds the flex sizing wrapper. */
  topPane: ReactNode
  /** Slot for the bottom half. */
  bottomPane: ReactNode
  /** Initial % of the available height the top pane should take in
   *  `split` mode. Defaults to 70 (table-favored). Drag-resize is
   *  internal — the consumer doesn't need to track pct. */
  initialTopPct?: number
  /** Override the wrapping class on the top pane. Defaults to the
   *  rounded-card style used by Reports and Database. Pass an empty
   *  string to render the slot bare (e.g. for a custom card). */
  topPaneClassName?: string
  /** Override the wrapping class on the bottom pane. Defaults to the
   *  bare positioning wrapper used by both consumers. */
  bottomPaneClassName?: string
  /** Override the outer split-container class. Defaults to the centered
   *  padded column used by both consumers. */
  className?: string
}

const DEFAULT_TOP_PANE_CLASS =
  'flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm'
const DEFAULT_BOTTOM_PANE_CLASS = 'relative flex min-h-0 flex-col'
const DEFAULT_CONTAINER_CLASS =
  'relative mx-auto flex min-h-0 w-full flex-1 flex-col overflow-hidden px-6 pt-6 lg:px-10'

/**
 * Vertical split layout: top pane (table) over bottom pane (chat) with
 * a drag-to-resize handle and four exclusive modes (split, top-min,
 * top-max, bottom-max). Extracted from `TableChatView` so the Database
 * view and any future surface with the same shape can reuse it.
 *
 * `mode` is controlled by the consumer so external events — like
 * sending a chat message while the table is maxed — can flip it back
 * to `split`. Drag-to-resize is internal; the pct is not exposed.
 */
export function SplitPane({
  mode,
  topPane,
  bottomPane,
  initialTopPct = 70,
  topPaneClassName,
  bottomPaneClassName,
  className,
}: SplitPaneProps) {
  const [topPct, setTopPct] = useState(initialTopPct)
  const splitRef = useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const onMove = (ev: MouseEvent) => {
      const el = splitRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const pct = ((ev.clientY - rect.top) / rect.height) * 100
      setTopPct(Math.min(Math.max(pct, 15), 85))
    }
    const onUp = () => setDragging(false)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [dragging])

  const showTop = mode !== 'bottom-max'
  const showBottom = mode !== 'top-max'
  const showHandle = mode === 'split'

  const topFlex =
    mode === 'split'
      ? { flex: `1 1 ${topPct}%`, minHeight: 0 }
      : mode === 'top-max'
        ? { flex: '1 1 auto', minHeight: 0 }
        : { flex: '0 0 auto' } // top-min — fits the header only
  const bottomFlex =
    mode === 'split'
      ? { flex: `1 1 ${100 - topPct}%`, minHeight: 0 }
      : { flex: '1 1 auto', minHeight: 0 }

  return (
    <div ref={splitRef} className={className ?? DEFAULT_CONTAINER_CLASS}>
      {showTop ? (
        <div style={topFlex} className={topPaneClassName ?? DEFAULT_TOP_PANE_CLASS}>
          {topPane}
        </div>
      ) : null}

      {showHandle ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize panes"
          onMouseDown={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          className={cn(
            'group flex h-2 shrink-0 cursor-row-resize items-center justify-center',
          )}
        >
          <div className="h-px w-full bg-transparent transition-colors group-hover:bg-border" />
          <GripHorizontal className="absolute size-4 text-muted-foreground/40 group-hover:text-muted-foreground" />
        </div>
      ) : null}

      {showBottom ? (
        <div
          style={bottomFlex}
          className={bottomPaneClassName ?? DEFAULT_BOTTOM_PANE_CLASS}
        >
          {bottomPane}
        </div>
      ) : null}
    </div>
  )
}
