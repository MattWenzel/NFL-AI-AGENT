import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ChevronDown,
  ChevronUp,
  Database,
  Maximize2,
  Minimize2,
  Save,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Composer } from '@/components/composer/Composer'
import { SplitPane, type SplitMode } from '@/components/layout/SplitPane'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { SqlEditorPanel } from '@/components/tables/SqlEditorPanel'
import { AuroraBackground } from '@/components/thread/AuroraBackground'
import { ApiError } from '@/lib/api'
import {
  runDatabaseQuery,
  type DatabaseQueryResult,
} from '@/lib/api/database'
import type { useDbHelperChat } from '@/lib/state/dbHelperChat'

import { HelperMessageList } from './HelperMessageList'
import { SaveAsReportDialog } from './SaveAsReportDialog'

const SQL_STORAGE_KEY = 'nfl-stats:database:last-sql'

interface DatabaseViewProps {
  /** Table the user picked from the sidebar (null = open without preselect). */
  selectedTable: string | null
  onSaveAsReport: (conversationId: string) => void
  /** SQL helper chat — render the transcript inline below the table and
   *  feed the docked composer through `helper.send`. */
  helper: ReturnType<typeof useDbHelperChat>
}

export interface DatabaseViewHandle {
  /** Replace the editor's SQL with `sql` and run it. Called by the SQL
   *  helper's `run_in_editor` tool to remote-control the editor. */
  runQuery: (sql: string) => void
}

function defaultQueryFor(tableName: string): string {
  return `SELECT * FROM ${tableName} LIMIT 100`
}

export const DatabaseView = forwardRef<DatabaseViewHandle, DatabaseViewProps>(function DatabaseView(
  { selectedTable, onSaveAsReport, helper },
  ref,
) {
  const [sql, setSql] = useState<string>(() => {
    if (typeof localStorage === 'undefined') return ''
    return localStorage.getItem(SQL_STORAGE_KEY) ?? ''
  })

  const [result, setResult] = useState<DatabaseQueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveOpen, setSaveOpen] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(SQL_STORAGE_KEY, sql)
    } catch {
      // quota / private mode — non-fatal
    }
  }, [sql])

  const runQuery = useCallback(async (sqlToRun: string) => {
    const trimmed = sqlToRun.trim()
    if (!trimmed) {
      setError('Type a SQL query first')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await runDatabaseQuery(trimmed)
      setResult(res)
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Query failed'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [])

  useImperativeHandle(
    ref,
    () => ({
      runQuery: (incoming: string) => {
        const next = incoming.trim()
        if (!next) return
        setSql(next)
        runQuery(next)
      },
    }),
    [runQuery],
  )

  const lastSelectedTableRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedTable) return
    if (lastSelectedTableRef.current === selectedTable) return
    lastSelectedTableRef.current = selectedTable
    const next = defaultQueryFor(selectedTable)
    setSql(next)
    runQuery(next)
  }, [selectedTable, runQuery])

  const tableForResult = useMemo(() => {
    if (!result) return null
    return {
      columns: result.columns,
      rows: result.rows,
      row_count: result.row_count,
      truncated: result.truncated,
      locked: false,
      last_sql: sql,
      updated_at: '',
    }
  }, [result, sql])

  const canSave = !!result && result.rows.length > 0

  const handleSaved = (conversationId: string) => {
    toast.success('Saved to Reports')
    onSaveAsReport(conversationId)
  }

  // Vertical split: table on top, helper transcript below. Mirrors
  // TableChatView's layout so Reports and Database feel the same.
  // Defaults to top-max (table-only) since the helper chat is opt-in;
  // the user opens it via the maximize-chat button.
  const [mode, setMode] = useState<SplitMode>('top-max')
  const tableMinimized = mode === 'top-min'
  const tableMaxed = mode === 'top-max'
  const chatMaxed = mode === 'bottom-max'
  const showTableBody = !tableMinimized

  // Auto-restore split when sending a message in maximized mode so the
  // helper's reply is visible.
  const handleSend: typeof helper.send = (message, opts) => {
    if (mode === 'top-max') setMode('split')
    return helper.send(message, opts)
  }

  const tablePane = (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-6 py-3">
        <Database className="size-4 text-muted-foreground" />
        <h1 className="text-lg font-semibold tracking-tight">
          Database
          {selectedTable ? (
            <span className="ml-2 font-mono text-sm font-normal text-muted-foreground">
              · {selectedTable}
            </span>
          ) : null}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="default"
            size="sm"
            onClick={() => setSaveOpen(true)}
            disabled={!canSave}
            title={canSave ? 'Save as Report' : 'Run a query that returns rows first'}
          >
            <Save className="size-4" />
            Save as Report
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
        </div>
      </div>

      {showTableBody ? (
        <>
          <div className="shrink-0 border-b border-border px-6 py-3">
            <SqlEditorPanel
              sql={sql}
              onSqlChange={setSql}
              onRun={() => runQuery(sql)}
              running={loading}
              error={error}
              defaultOpen={false}
              placeholder="SELECT * FROM players LIMIT 100"
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col">
            <LiveTableView
              table={tableForResult}
              loading={loading}
              error={null}
              emptyHint="Pick a table on the left, or write a query above and run it."
            />
          </div>
        </>
      ) : null}
    </>
  )

  const chatPane = (
    <>
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1">
        {helper.messages.length > 0 && !helper.streaming ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground hover:text-foreground"
            onClick={helper.clear}
            aria-label="Clear helper chat"
            title="Clear helper chat"
          >
            <Trash2 className="size-4" />
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          onClick={() => setMode(chatMaxed ? 'split' : 'bottom-max')}
          aria-pressed={chatMaxed}
          aria-label={chatMaxed ? 'Restore split' : 'Maximize chat'}
          title={chatMaxed ? 'Restore split' : 'Maximize chat'}
        >
          {chatMaxed ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </Button>
      </div>
      <HelperMessageList
        messages={helper.messages}
        streaming={helper.streaming}
        error={helper.error}
      />
    </>
  )

  return (
    <div className="relative flex min-h-0 w-full flex-1 flex-col overflow-hidden">
      <AuroraBackground />
      <SplitPane mode={mode} topPane={tablePane} bottomPane={chatPane} />

      <Composer
        streaming={helper.streaming}
        onSend={(message, opts) => handleSend(message, opts)}
        onStop={helper.stop}
        placeholder="Ask about the schema, plan a query, or paste SQL…"
      />

      <SaveAsReportDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        sql={sql}
        result={result}
        onCreated={handleSaved}
      />
    </div>
  )
})
