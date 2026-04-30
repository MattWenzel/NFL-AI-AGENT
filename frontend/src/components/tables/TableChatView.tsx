import { useCallback, useEffect, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Download,
  Lock,
  Maximize2,
  Minimize2,
  Pencil,
  Trash2,
  Unlock,
} from 'lucide-react'
import { toast } from 'sonner'

import { Composer } from '@/components/composer/Composer'
import { SplitPane, type SplitMode } from '@/components/layout/SplitPane'
import { EmptyTableChat } from '@/components/tables/EmptyTableChat'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { SqlEditorPanel } from '@/components/tables/SqlEditorPanel'
import { AuroraBackground } from '@/components/thread/AuroraBackground'
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
import { useChatContext } from '@/lib/state/chatContext'
import { sanitizeCsvFilename, tableToCsv } from '@/lib/csv'
import { useTablesContext } from '@/lib/state/tablesContext'
import { runReportSql, setTableLocked } from '@/lib/api/tables'
import type { TableState } from '@/lib/api/tables'

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

  // Vertical split between table (top) and chat (bottom). The split layout
  // primitive owns drag-to-resize internally; we just track which mode the
  // user has chosen so the maximize/minimize buttons work and so we can
  // flip back to `split` when sending a message.
  const [mode, setMode] = useState<SplitMode>('split')
  const tableMinimized = mode === 'top-min'
  const tableMaxed = mode === 'top-max'
  const chatMaxed = mode === 'bottom-max'
  const showTableBody = !tableMinimized

  // Sending a message while the table is fully maximized would otherwise
  // hide the agent's response — auto-drop back to the split layout so the
  // user sees the reply immediately.
  const handleSend: typeof onSend = (message, opts) => {
    if (mode === 'top-max') setMode('split')
    onSend(message, opts)
  }

  const transcript = chat.transcript
  const hasTurns = !!transcript && transcript.turns.length > 0
  const chatStreaming = chat.streamStatus === 'streaming'

  const tablePane = (
    <>
      <header className="flex shrink-0 items-start gap-3 border-b border-border px-6 py-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
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
            <span className="hidden sm:inline">{isLocked ? 'Locked' : 'Lock'}</span>
          </Button>
          <Button size="sm" onClick={downloadCsv} disabled={!hasRows} aria-label="Download CSV">
            <Download className="size-4" />
            <span className="hidden sm:inline">Download</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground hover:text-foreground"
            onClick={() => setMode(tableMinimized ? 'split' : 'top-min')}
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
            onClick={() => setMode(tableMaxed ? 'split' : 'top-max')}
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
    </>
  )

  const chatPane = (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="absolute right-2 top-2 z-10 size-7 text-muted-foreground hover:text-foreground"
        onClick={() => setMode(chatMaxed ? 'split' : 'bottom-max')}
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
    </>
  )

  return (
    <div className="relative flex min-h-0 w-full flex-1 flex-col overflow-hidden">
      <AuroraBackground />
      <SplitPane mode={mode} topPane={tablePane} bottomPane={chatPane} />

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
