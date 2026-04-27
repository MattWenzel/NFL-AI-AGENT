import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Check, ChevronRight, Copy, Database, MessageSquare, Play, Save } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { useLayout } from '@/components/layout/AppShell'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api'
import {
  runDatabaseQuery,
  type DatabaseQueryResult,
} from '@/lib/database'
import { cn } from '@/lib/utils'

import { SaveAsReportDialog } from './SaveAsReportDialog'

const SQL_STORAGE_KEY = 'nfl-stats:database:last-sql'
const TABLE_STORAGE_KEY = 'nfl-stats:database:last-table'

interface DatabaseViewProps {
  /** Table the user picked from the sidebar (null = open without preselect). */
  selectedTable: string | null
  onSelectedTableChange: (next: string | null) => void
  onSaveAsReport: (conversationId: string) => void
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
  { selectedTable, onSelectedTableChange, onSaveAsReport },
  ref,
) {
  // SQL editor — restored from localStorage on first mount; cleared back to
  // an auto-generated `SELECT *` when the user picks a table from the
  // sidebar.
  const [sql, setSql] = useState<string>(() => {
    if (typeof localStorage === 'undefined') return ''
    return localStorage.getItem(SQL_STORAGE_KEY) ?? ''
  })

  const [result, setResult] = useState<DatabaseQueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveOpen, setSaveOpen] = useState(false)
  const [sqlOpen, setSqlOpen] = useState(true)
  const [sqlCopied, setSqlCopied] = useState(false)

  const copySql = useCallback(async () => {
    const trimmed = sql.trim()
    if (!trimmed) return
    try {
      await navigator.clipboard.writeText(trimmed)
      setSqlCopied(true)
      setTimeout(() => setSqlCopied(false), 1500)
    } catch {
      toast.error('Could not copy — your browser blocked clipboard access')
    }
  }, [sql])

  // Persist SQL to localStorage so reloading the tab doesn't wipe the
  // user's in-progress query.
  useEffect(() => {
    try {
      localStorage.setItem(SQL_STORAGE_KEY, sql)
    } catch {
      // quota / private mode — non-fatal
    }
  }, [sql])

  useEffect(() => {
    try {
      if (selectedTable) localStorage.setItem(TABLE_STORAGE_KEY, selectedTable)
    } catch {
      // ignore
    }
  }, [selectedTable])

  // Restore the previously-picked table on first mount when the parent
  // didn't pass one in.
  const didRestoreRef = useRef(false)
  useEffect(() => {
    if (didRestoreRef.current) return
    didRestoreRef.current = true
    if (selectedTable) return
    try {
      const stored = localStorage.getItem(TABLE_STORAGE_KEY)
      if (stored) onSelectedTableChange(stored)
    } catch {
      // ignore
    }
  }, [selectedTable, onSelectedTableChange])

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

  // Imperative `runQuery(sql)` for the SQL helper's `run_in_editor`
  // remote-control. The helper agent calls a tool, the SSE event flows
  // through useDbHelperChat in App.tsx, which forwards the SQL here.
  // Updates the editor and runs in one step so the user sees both.
  useImperativeHandle(
    ref,
    () => ({
      runQuery: (incoming: string) => {
        const next = incoming.trim()
        if (!next) return
        setSql(next)
        // Skip the natural "selectedTable changed → auto-run" race by
        // running directly with the helper's SQL rather than letting the
        // table-change effect overwrite it.
        runQuery(next)
      },
    }),
    [runQuery],
  )

  // When the user picks a new table from the sidebar, replace the editor
  // contents with a default `SELECT *` and immediately run it. We compare
  // against the last-handled selection so editing the SQL after picking
  // doesn't get clobbered every render.
  const lastSelectedTableRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedTable) return
    if (lastSelectedTableRef.current === selectedTable) return
    lastSelectedTableRef.current = selectedTable
    const next = defaultQueryFor(selectedTable)
    setSql(next)
    runQuery(next)
  }, [selectedTable, runQuery])

  // LiveTableView's `table` prop is typed as `TableState` (which is shared
  // with the table-chat view) and includes `locked` / `last_sql` /
  // `updated_at`. The renderer only reads columns/rows/row_count/truncated
  // — fill the rest with neutral defaults to satisfy the shape.
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

  // The SQL helper lives in the right pane; expose a toolbar toggle when
  // the panel is closed so the user can reopen it without a floating CTA
  // overlapping the table.
  const layout = useLayout()

  return (
    <div className="mx-auto flex min-h-0 w-full flex-1 flex-col px-6 pt-6 pb-3 lg:px-10">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
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
            variant="outline"
            size="sm"
            onClick={() => runQuery(sql)}
            disabled={loading || !sql.trim()}
          >
            <Play className="size-4" />
            Run
          </Button>
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
          {!layout.desktopInspectorOpen ? (
            <Button
              variant="outline"
              size="sm"
              onClick={layout.toggleDesktopInspector}
              title="Open SQL helper"
            >
              <MessageSquare className="size-4" />
              SQL helper
            </Button>
          ) : null}
        </div>
      </div>

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
              {!sqlOpen && sql.trim() ? (
                <span className="truncate font-mono text-2xs text-muted-foreground/80">
                  {sql.trim().split('\n')[0].slice(0, 120)}
                  {sql.trim().split('\n').length > 1 || sql.trim().length > 120 ? ' …' : ''}
                </span>
              ) : null}
            </CollapsibleTrigger>
            <button
              type="button"
              onClick={copySql}
              disabled={!sql.trim()}
              aria-label="Copy SQL"
              title={sqlCopied ? 'Copied' : 'Copy SQL'}
              className={cn(
                'flex shrink-0 items-center gap-1 px-3 text-2xs text-muted-foreground transition-colors',
                'hover:bg-muted/40 hover:text-foreground disabled:opacity-50',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
              )}
            >
              {sqlCopied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              <span className="font-medium uppercase tracking-[0.14em]">
                {sqlCopied ? 'Copied' : 'Copy'}
              </span>
            </button>
          </div>
          <CollapsibleContent className="border-t border-border bg-background/50">
            <Textarea
              className="min-h-[120px] resize-y rounded-none border-0 bg-transparent font-mono text-sm focus-visible:ring-0 focus-visible:ring-offset-0"
              spellCheck={false}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                  e.preventDefault()
                  runQuery(sql)
                }
              }}
              placeholder="SELECT * FROM players LIMIT 100"
            />
            <p className="border-t border-border px-3 py-1.5 text-2xs text-muted-foreground">
              Read-only. Press{' '}
              <kbd className="rounded border border-border bg-muted px-1">⌘/Ctrl</kbd>
              <kbd className="ml-1 rounded border border-border bg-muted px-1">Enter</kbd> to run.
              Up to 500 rows.
            </p>
          </CollapsibleContent>
        </Collapsible>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <LiveTableView
          table={tableForResult}
          loading={loading}
          error={error}
          emptyHint="Pick a table on the left, or write a query above and run it."
        />
      </div>
      </div>

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
