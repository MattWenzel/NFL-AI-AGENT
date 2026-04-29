import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  ChevronRight,
  Copy,
  Download,
  Lock,
  Maximize2,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Play,
  Trash2,
  Unlock,
} from 'lucide-react'
import { toast } from 'sonner'

import { Composer } from '@/components/composer/Composer'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { Thread } from '@/components/thread/Thread'
import { EmptyTableChat } from '@/components/tables/EmptyTableChat'
import { useLayout } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api'
import { useChatContext } from '@/lib/chatContext'
import { sanitizeCsvFilename, tableToCsv } from '@/lib/csv'
import { useTablesContext } from '@/lib/tablesContext'
import { cn } from '@/lib/utils'
import { runReportSql, setTableLocked } from '@/lib/tables'
import type { TableState } from '@/lib/tables'

interface TableChatViewProps {
  activeTableId: string
  /** Live table state, lifted to App so the create-report flow can refetch
   *  via the SSE event handler the same way the edit flow does. */
  table: TableState | null
  tableLoading: boolean
  tableError: string | null
  refetchTable: () => Promise<void>
  /** Programmatic close — currently fired only after a successful delete. */
  onClose: () => void
  /** Called when the agent's `create_report` tool fires — used to navigate
   *  to the newly created report. Wired identically to the regular-chat
   *  composer in App.tsx. */
  onReportCreated?: (payload: { report_id: string; title: string; row_count: number }) => void
}

const SPLIT_STORAGE_KEY = 'nfl-stats:table-chat-split-px'
const DEFAULT_BOTTOM_PX = 320
const MIN_BOTTOM_PX = 200
const MAX_BOTTOM_PX_FROM_TOP = 200 // floor for the table region

function readSplit(): number {
  if (typeof window === 'undefined') return DEFAULT_BOTTOM_PX
  try {
    const raw = window.localStorage.getItem(SPLIT_STORAGE_KEY)
    const n = raw ? Number(raw) : NaN
    return Number.isFinite(n) && n >= MIN_BOTTOM_PX ? n : DEFAULT_BOTTOM_PX
  } catch {
    return DEFAULT_BOTTOM_PX
  }
}

function writeSplit(px: number) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(px)))
  } catch {
    // non-fatal
  }
}

export function TableChatView({
  activeTableId,
  table,
  tableLoading,
  tableError,
  refetchTable,
  onClose,
  onReportCreated,
}: TableChatViewProps) {
  const chat = useChatContext()
  const tables = useTablesContext()
  const layout = useLayout()
  // Local aliases keep the rest of the component readable.
  const loading = tableLoading
  const error = tableError
  const refetch = refetchTable

  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [bottomPx, setBottomPx] = useState<number>(() => readSplit())
  const [sqlCopied, setSqlCopied] = useState(false)
  // "Expanded" focus mode: collapses the chat region to its floor, closes
  // the inspector, and remembers the prior split so the user can restore.
  const [expanded, setExpanded] = useState(false)
  const preExpandPxRef = useRef<number | null>(null)

  // Drag-to-resize the divider. The container ref bounds the resize so the
  // user can't drag the table region below MAX_BOTTOM_PX_FROM_TOP.
  const containerRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef<boolean>(false)

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const next = rect.bottom - e.clientY
      const clamped = Math.max(
        MIN_BOTTOM_PX,
        Math.min(rect.height - MAX_BOTTOM_PX_FROM_TOP, next),
      )
      setBottomPx(clamped)
    }
    const onUp = () => {
      if (!draggingRef.current) return
      draggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      writeSplit(bottomPx)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [bottomPx])

  // Title is sourced from the conversations list (which the tables tab shares
  // shape-wise). Falls back to a placeholder while the list is loading.
  const tableInfo = tables.tables.find((t) => t.id === activeTableId)
  const title = tableInfo?.title || 'Untitled report'

  const startRename = () => {
    setRenameValue(title)
    setRenameOpen(true)
  }
  const submitRename = async () => {
    const next = renameValue.trim()
    if (!next || next === title) {
      setRenameOpen(false)
      return
    }
    setRenaming(true)
    try {
      await tables.rename(activeTableId, next)
      toast.success('Renamed')
      setRenameOpen(false)
    } catch {
      toast.error('Could not rename')
    } finally {
      setRenaming(false)
    }
  }

  const [lockBusy, setLockBusy] = useState(false)
  const isLocked = !!table?.locked

  const toggleLock = useCallback(async () => {
    if (!table || lockBusy) return
    setLockBusy(true)
    try {
      await setTableLocked(activeTableId, !table.locked)
      await refetch()
      toast.success(table.locked ? 'Table unlocked' : 'Table locked')
    } catch {
      toast.error('Could not change lock state')
    } finally {
      setLockBusy(false)
    }
  }, [activeTableId, lockBusy, refetch, table])

  const submitDelete = async () => {
    // Tear down the view *before* the network round-trip so the user
    // immediately lands on the new-report screen — `tables.remove` swallows
    // its own errors and refreshes the list, so the only thing that can
    // delay the navigation is the await itself.
    setConfirmDeleteOpen(false)
    onClose()
    try {
      await tables.remove(activeTableId)
      toast.success('Report deleted')
    } catch {
      toast.error('Could not delete report')
    }
  }

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => {
      if (!prev) {
        // Entering expanded: remember the current split, collapse the chat
        // region to its floor, and dismiss the inspector so the table fills
        // the screen.
        preExpandPxRef.current = bottomPx
        setBottomPx(MIN_BOTTOM_PX)
        layout.closeDesktopInspector()
        return true
      }
      // Exiting: restore the saved split (or fall back to the default).
      setBottomPx(preExpandPxRef.current ?? DEFAULT_BOTTOM_PX)
      preExpandPxRef.current = null
      return false
    })
  }, [bottomPx, layout])

  const sendTurn = useCallback(
    (
      message: string,
      opts: {
        provider: string
        model: string
        toolChoice: 'auto' | 'required' | 'none'
      },
    ) => {
      chat
        .send(message, {
          provider: opts.provider,
          model: opts.model,
          toolChoice: opts.toolChoice,
          onTableUpdated: () => {
            // Refetch the live table on every successful set_table call so
            // the UI mirrors the persisted state without trusting the SSE
            // payload alone.
            refetch()
          },
          onReportCreated,
        })
        .finally(() => {
          // Refresh the tables list so the sidebar picks up the projected
          // title from the first user turn (the SQL COALESCE falls through
          // to "New conversation" until at least one user turn exists).
          tables.refresh()
        })
    },
    [chat, refetch, onReportCreated, tables],
  )

  const hasRows = !!table && table.rows.length > 0

  // Editable draft of the live table's SQL. Mirrors `table?.last_sql` and
  // resyncs whenever the upstream value changes (agent ran `set_table`, or
  // we just successfully ran user-edited SQL and refetched). In-flight local
  // edits are clobbered by an upstream change — same trade-off as the
  // Database editor.
  const [sqlDraft, setSqlDraft] = useState<string>(table?.last_sql ?? '')
  const [sqlRunning, setSqlRunning] = useState(false)
  const [sqlError, setSqlError] = useState<string | null>(null)
  const [sqlOpen, setSqlOpen] = useState(true)

  useEffect(() => {
    setSqlDraft(table?.last_sql ?? '')
    setSqlError(null)
  }, [table?.last_sql])

  const copySourceSql = useCallback(async () => {
    const sql = sqlDraft || table?.last_sql
    if (!sql) return
    try {
      await navigator.clipboard.writeText(sql)
      setSqlCopied(true)
      setTimeout(() => setSqlCopied(false), 1500)
    } catch {
      toast.error('Could not copy — your browser blocked clipboard access')
    }
  }, [sqlDraft, table?.last_sql])

  const runSql = useCallback(async () => {
    const trimmed = sqlDraft.trim()
    if (!trimmed || sqlRunning || isLocked) return
    setSqlRunning(true)
    setSqlError(null)
    try {
      await runReportSql(activeTableId, trimmed)
      await refetch()
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Query failed'
      setSqlError(message)
    } finally {
      setSqlRunning(false)
    }
  }, [activeTableId, isLocked, refetch, sqlDraft, sqlRunning])

  const downloadCsv = useCallback(() => {
    if (!table || table.rows.length === 0) return
    const csv = tableToCsv(table.columns, table.rows)
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = sanitizeCsvFilename(title)
    document.body.appendChild(a)
    a.click()
    a.remove()
    // Defer revoke until after the download navigation has resolved.
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }, [table, title])

  return (
    <div className="flex min-h-0 flex-1 flex-col" ref={containerRef}>
      {/* Top: title bar + live table, wrapped in a card. Horizontal width
          matches the chat thread's `max-w-6xl mx-auto` so the report aligns
          with the chat bubbles below — UNLESS expanded (focus) mode is on,
          in which case the card stretches to fill the available width. */}
      <div
        className={cn(
          'mx-auto flex min-h-0 w-full flex-1 flex-col overflow-hidden px-6 pt-6 pb-3 lg:px-10',
          // Scale the cap with viewport: 7xl on medium screens, screen-2xl
          // (~1536px) on 2xl displays so the table uses extra room without
          // stretching across an entire ultrawide. Expanded mode always fills.
          !expanded && 'max-w-7xl 2xl:max-w-screen-2xl',
        )}
      >
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          <header className="flex shrink-0 items-start gap-3 border-b border-border px-6 py-4">
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Report
              </p>
              <div className="flex items-center gap-2">
                <h1 className="truncate font-display text-2xl font-medium tracking-tight">{title}</h1>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  onClick={startRename}
                  aria-label="Rename report"
                >
                  <Pencil className="size-4" />
                </Button>
              </div>
              {table ? (
                <p className="text-2xs text-muted-foreground">
                  {table.row_count.toLocaleString()} rows · {table.columns.length} columns
                  {table.truncated ? ' · capped' : ''}
                  {isLocked ? ' · locked' : ''}
                </p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant={isLocked ? 'secondary' : 'ghost'}
                size="sm"
                onClick={toggleLock}
                disabled={!table || lockBusy}
                aria-pressed={isLocked}
                aria-label={isLocked ? 'Unlock table' : 'Lock table'}
                title={
                  isLocked
                    ? 'Table is locked — the agent cannot change it. Click to unlock.'
                    : 'Lock the table so the agent cannot change it.'
                }
              >
                {isLocked ? <Lock className="size-4" /> : <Unlock className="size-4" />}
                {isLocked ? 'Locked' : 'Lock'}
              </Button>
              <Button size="sm" onClick={downloadCsv} disabled={!hasRows}>
                <Download className="size-4" />
                Download
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setConfirmDeleteOpen(true)}
                aria-label="Delete report"
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleExpanded}
                aria-label={expanded ? 'Restore default layout' : 'Expand table'}
                aria-pressed={expanded}
                className="text-muted-foreground hover:text-foreground"
              >
                {expanded ? (
                  <Minimize2 className="size-4" />
                ) : (
                  <Maximize2 className="size-4" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={layout.toggleDesktopInspector}
                aria-label={layout.desktopInspectorOpen ? 'Close inspector' : 'Open inspector'}
                aria-pressed={layout.desktopInspectorOpen}
                className="text-muted-foreground hover:text-foreground"
              >
                {layout.desktopInspectorOpen ? (
                  <PanelRightClose className="size-4" />
                ) : (
                  <PanelRightOpen className="size-4" />
                )}
              </Button>
            </div>
          </header>
          {table?.last_sql ? (
            <div className="shrink-0 border-b border-border px-6 py-3">
              <Collapsible
                open={sqlOpen}
                onOpenChange={setSqlOpen}
                className="overflow-hidden rounded-lg border border-border bg-muted/20"
              >
                <div className="flex items-stretch">
                  <CollapsibleTrigger
                    className={cn(
                      'group flex flex-1 items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/40',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                    )}
                  >
                    <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-90" />
                    <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      SQL
                    </span>
                    {!sqlOpen && sqlDraft.trim() ? (
                      <span className="truncate font-mono text-2xs text-muted-foreground/80">
                        {sqlDraft.trim().split('\n')[0].slice(0, 120)}
                        {sqlDraft.trim().split('\n').length > 1 || sqlDraft.trim().length > 120 ? ' …' : ''}
                      </span>
                    ) : null}
                  </CollapsibleTrigger>
                  <button
                    type="button"
                    onClick={runSql}
                    disabled={sqlRunning || isLocked || !sqlDraft.trim()}
                    aria-label="Run SQL"
                    title={
                      isLocked
                        ? 'Unlock the table to edit and run SQL'
                        : 'Run SQL (⌘/Ctrl+Enter)'
                    }
                    className={cn(
                      'flex shrink-0 items-center gap-1 px-3 text-2xs text-muted-foreground transition-colors',
                      'hover:bg-muted/40 hover:text-foreground disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-muted-foreground',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                    )}
                  >
                    <Play className="size-3.5" />
                    <span className="font-medium uppercase tracking-[0.14em]">
                      {sqlRunning ? 'Running' : 'Run'}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={copySourceSql}
                    disabled={!sqlDraft.trim()}
                    aria-label="Copy SQL"
                    title={sqlCopied ? 'Copied' : 'Copy SQL'}
                    className={cn(
                      'flex shrink-0 items-center gap-1 px-3 text-2xs text-muted-foreground transition-colors',
                      'hover:bg-muted/40 hover:text-foreground disabled:opacity-50',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                    )}
                  >
                    {sqlCopied ? (
                      <Check className="size-3.5" />
                    ) : (
                      <Copy className="size-3.5" />
                    )}
                    <span className="font-medium uppercase tracking-[0.14em]">
                      {sqlCopied ? 'Copied' : 'Copy'}
                    </span>
                  </button>
                </div>
                <CollapsibleContent className="border-t border-border bg-background/50">
                  <Textarea
                    className="min-h-[120px] max-h-[18rem] resize-y overflow-auto rounded-none border-0 bg-transparent font-mono text-xs leading-relaxed [field-sizing:fixed] focus-visible:ring-0 focus-visible:ring-offset-0"
                    spellCheck={false}
                    value={sqlDraft}
                    onChange={(e) => setSqlDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                        e.preventDefault()
                        runSql()
                      }
                    }}
                    readOnly={isLocked}
                    placeholder="SELECT ..."
                  />
                  <p className="border-t border-border px-3 py-1.5 text-2xs text-muted-foreground">
                    {isLocked ? (
                      <>Table is locked — unlock to edit and run.</>
                    ) : (
                      <>
                        Read-only. Press{' '}
                        <kbd className="rounded border border-border bg-muted px-1">⌘/Ctrl</kbd>
                        <kbd className="ml-1 rounded border border-border bg-muted px-1">Enter</kbd>{' '}
                        to run. Up to 500 rows.
                      </>
                    )}
                  </p>
                  {sqlError ? (
                    <p className="border-t border-border bg-destructive/10 px-3 py-1.5 text-2xs text-destructive">
                      {sqlError}
                    </p>
                  ) : null}
                </CollapsibleContent>
              </Collapsible>
            </div>
          ) : null}
          <LiveTableView table={table} loading={loading} error={error} />
        </div>
      </div>

      {/* Drag handle — invisible by default; the natural seam between the
          report card and the chat region above carries the visual divider.
          The grab pill fades in on hover so the affordance is discoverable
          without being noisy. */}
      <div
        role="separator"
        aria-orientation="horizontal"
        onMouseDown={onDragStart}
        className="group flex h-2 shrink-0 cursor-row-resize items-center justify-center"
      >
        <div className="h-0.5 w-10 rounded-full bg-muted-foreground/40 opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
      </div>

      {/* Bottom: full scrollable transcript + composer. The transcript
          renders the whole conversation, but Thread auto-scrolls the
          latest user message to the top of the visible region on send so
          the newest exchange is always in view. */}
      <div
        className="flex min-h-[200px] shrink-0 flex-col overflow-hidden border-t border-border bg-background"
        style={{ height: bottomPx }}
      >
        {chat.transcript && chat.transcript.turns.length > 0 ? (
          <Thread transcript={chat.transcript} />
        ) : (
          <EmptyTableChat streaming={chat.streamStatus === 'streaming'} />
        )}
        {chat.streamError ? (
          <div className="px-4 py-2 text-center text-xs text-destructive">{chat.streamError}</div>
        ) : null}
        <Composer
          variant="docked"
          placeholder={
            isLocked
              ? 'Table is locked — ask questions or unlock to make changes.'
              : 'Ask the agent to refine the table or answer a question…'
          }
          streaming={chat.streamStatus === 'streaming'}
          onSend={sendTurn}
          onStop={chat.stop}
        />
      </div>

      {/* Rename dialog */}
      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename report</DialogTitle>
            <DialogDescription>This is also the default filename when you download.</DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                submitRename()
              }
            }}
            maxLength={200}
            placeholder="Report title"
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameOpen(false)} disabled={renaming}>
              Cancel
            </Button>
            <Button
              onClick={submitRename}
              disabled={renaming || !renameValue.trim() || renameValue.trim() === title}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Confirm delete dialog */}
      <Dialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this report?</DialogTitle>
            <DialogDescription>
              The chat history and the current table will be removed permanently.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={submitDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
