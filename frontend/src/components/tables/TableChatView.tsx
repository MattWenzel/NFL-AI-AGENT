import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Download,
  GripHorizontal,
  Lock,
  Maximize2,
  Minimize2,
  Pencil,
  Trash2,
  Unlock,
} from 'lucide-react'
import { toast } from 'sonner'

import { Composer } from '@/components/composer/Composer'
import { EmptyTableChat } from '@/components/tables/EmptyTableChat'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { SqlEditorPanel } from '@/components/tables/SqlEditorPanel'
import { Thread } from '@/components/thread/Thread'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ApiError } from '@/lib/api'
import { useChatContext } from '@/lib/chatContext'
import { sanitizeCsvFilename, tableToCsv } from '@/lib/csv'
import { useTablesContext } from '@/lib/tablesContext'
import { runReportSql, setTableLocked } from '@/lib/tables'
import type { TableState } from '@/lib/tables'

type SplitMode = 'split' | 'table-min' | 'table-max' | 'chat-max'

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
  onSend: (
    message: string,
    opts: { provider: string; model: string; toolChoice: 'auto' | 'required' | 'none' },
  ) => void
  streaming: boolean
  onStop: () => void
}

export function TableChatView({
  activeTableId,
  table,
  tableLoading,
  tableError,
  refetchTable,
  onClose,
  onSend,
  streaming,
  onStop,
}: TableChatViewProps) {
  const tables = useTablesContext()
  const chat = useChatContext()
  const loading = tableLoading
  const error = tableError
  const refetch = refetchTable

  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

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
    setConfirmDeleteOpen(false)
    onClose()
    try {
      await tables.remove(activeTableId)
      toast.success('Report deleted')
    } catch {
      toast.error('Could not delete report')
    }
  }

  const hasRows = !!table && table.rows.length > 0

  const [sqlDraft, setSqlDraft] = useState<string>(table?.last_sql ?? '')
  const [sqlRunning, setSqlRunning] = useState(false)
  const [sqlError, setSqlError] = useState<string | null>(null)

  useEffect(() => {
    setSqlDraft(table?.last_sql ?? '')
    setSqlError(null)
  }, [table?.last_sql])

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
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }, [table, title])

  // Vertical split between table (top) and chat (bottom). `tablePct` is the
  // table's share of the available height when in split mode; the rest goes
  // to the chat. `mode` lets the user maximize either section to take 100%.
  const [tablePct, setTablePct] = useState(55)
  const [mode, setMode] = useState<SplitMode>('split')
  const splitRef = useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const onMove = (ev: MouseEvent) => {
      const el = splitRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const pct = ((ev.clientY - rect.top) / rect.height) * 100
      setTablePct(Math.min(Math.max(pct, 15), 85))
    }
    const onUp = () => setDragging(false)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [dragging])

  const tableMinimized = mode === 'table-min'
  const tableMaxed = mode === 'table-max'
  const chatMaxed = mode === 'chat-max'
  const showTable = !chatMaxed
  const showTableBody = !tableMinimized
  const showChat = !tableMaxed
  const showHandle = mode === 'split'

  const tableFlex =
    mode === 'split'
      ? { flex: `1 1 ${tablePct}%`, minHeight: 0 }
      : mode === 'table-max'
        ? { flex: '1 1 auto', minHeight: 0 }
        : { flex: '0 0 auto' } // minimized — fits the header only
  const chatFlex =
    mode === 'split'
      ? { flex: `1 1 ${100 - tablePct}%`, minHeight: 0 }
      : { flex: '1 1 auto', minHeight: 0 }

  // Sending a message while the table is fully maximized would otherwise
  // hide the agent's response — auto-drop back to the split layout so the
  // user sees the reply immediately.
  const handleSend: typeof onSend = (message, opts) => {
    if (mode === 'table-max') setMode('split')
    onSend(message, opts)
  }

  const transcript = chat.transcript
  const hasTurns = !!transcript && transcript.turns.length > 0
  const chatStreaming = chat.streamStatus === 'streaming'

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col">
      <div
        ref={splitRef}
        className="mx-auto flex min-h-0 w-full flex-1 flex-col overflow-hidden px-6 pt-6 lg:px-10"
      >
        {showTable ? (
          <div
            style={tableFlex}
            className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm"
          >
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
                  className="size-8 text-muted-foreground hover:text-foreground"
                  onClick={() => setMode(tableMinimized ? 'split' : 'table-min')}
                  aria-pressed={tableMinimized}
                  aria-label={tableMinimized ? 'Restore split' : 'Minimize table'}
                  title={tableMinimized ? 'Restore split' : 'Minimize table'}
                >
                  {tableMinimized ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-muted-foreground hover:text-foreground"
                  onClick={() => setMode(tableMaxed ? 'split' : 'table-max')}
                  aria-pressed={tableMaxed}
                  aria-label={tableMaxed ? 'Restore split' : 'Maximize table'}
                  title={tableMaxed ? 'Restore split' : 'Maximize table'}
                >
                  {tableMaxed ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
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
              </div>
            </header>
            {showTableBody ? (
              <>
                {table?.last_sql ? (
                  <div className="shrink-0 border-b border-border px-6 py-3">
                    <SqlEditorPanel
                      sql={sqlDraft}
                      onSqlChange={setSqlDraft}
                      onRun={runSql}
                      running={sqlRunning}
                      error={sqlError}
                      locked={isLocked}
                      defaultOpen={false}
                    />
                  </div>
                ) : null}
                <LiveTableView table={table} loading={loading} error={error} />
              </>
            ) : null}
          </div>
        ) : null}

        {showHandle ? (
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize table and chat"
            onMouseDown={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            className="group flex h-2 shrink-0 cursor-row-resize items-center justify-center"
          >
            <div className="h-px w-full bg-transparent transition-colors group-hover:bg-border" />
            <GripHorizontal className="absolute size-4 text-muted-foreground/40 group-hover:text-muted-foreground" />
          </div>
        ) : null}

        {showChat ? (
          <div style={chatFlex} className="relative flex min-h-0 flex-col">
            <Button
              variant="ghost"
              size="icon"
              className="absolute right-2 top-2 z-10 size-7 text-muted-foreground hover:text-foreground"
              onClick={() => setMode(chatMaxed ? 'split' : 'chat-max')}
              aria-pressed={chatMaxed}
              aria-label={chatMaxed ? 'Restore split' : 'Maximize chat'}
              title={chatMaxed ? 'Restore split' : 'Maximize chat'}
            >
              {chatMaxed ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            </Button>
            {hasTurns ? (
              <Thread transcript={transcript!} />
            ) : (
              <EmptyTableChat streaming={chatStreaming} />
            )}
            {chat.streamError ? (
              <div className="px-4 py-2 text-center text-xs text-destructive">
                {chat.streamError}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <Composer
        streaming={streaming}
        onSend={handleSend}
        onStop={onStop}
        placeholder={
          isLocked
            ? 'Table is locked — ask questions or unlock to make changes.'
            : 'Ask the agent to refine the table or answer a question…'
        }
      />

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
