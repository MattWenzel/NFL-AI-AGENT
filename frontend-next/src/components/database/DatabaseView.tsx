import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Database, Play, Save } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { LiveTableView } from '@/components/tables/LiveTableView'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api'
import {
  fetchDatabaseTables,
  runDatabaseQuery,
  type DatabaseQueryResult,
  type DatabaseTableInfo,
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

function defaultQueryFor(tableName: string): string {
  return `SELECT * FROM ${tableName} LIMIT 100`
}

function isoDateOnly(): string {
  return new Date().toISOString().slice(0, 10)
}

export function DatabaseView({
  selectedTable,
  onSelectedTableChange,
  onSaveAsReport,
}: DatabaseViewProps) {
  const [tables, setTables] = useState<DatabaseTableInfo[] | null>(null)
  const [tablesError, setTablesError] = useState(false)

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

  // Load the table list once.
  useEffect(() => {
    let alive = true
    fetchDatabaseTables()
      .then((rows) => {
        if (alive) setTables(rows)
      })
      .catch(() => {
        if (alive) setTablesError(true)
      })
    return () => {
      alive = false
    }
  }, [])

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

  const tableOptions = useMemo(() => tables ?? [], [tables])

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
  const defaultReportTitle = selectedTable
    ? `${selectedTable} — ${isoDateOnly()}`
    : `Query result — ${isoDateOnly()}`

  const handleSaved = (conversationId: string) => {
    toast.success('Saved to Reports')
    onSaveAsReport(conversationId)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Database className="size-4 text-muted-foreground" />
        <h1 className="text-lg font-semibold tracking-tight">Database</h1>
        <div className="ml-auto flex items-center gap-2">
          <select
            className={cn(
              'h-9 rounded-md border border-input bg-background px-3 text-sm',
              'focus:outline-none focus:ring-1 focus:ring-ring',
            )}
            value={selectedTable ?? ''}
            onChange={(e) => {
              const next = e.target.value || null
              onSelectedTableChange(next)
            }}
            disabled={tablesError || tableOptions.length === 0}
          >
            <option value="">
              {tablesError
                ? "Couldn't load tables"
                : tableOptions.length === 0
                  ? 'Loading tables…'
                  : 'Pick a table…'}
            </option>
            {tableOptions.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
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
        </div>
      </div>

      <div className="border-b border-border px-4 py-3">
        <Textarea
          className="min-h-[120px] resize-y font-mono text-sm"
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
        <p className="mt-1 text-xs text-muted-foreground">
          Read-only. Press <kbd className="rounded border border-border bg-muted px-1">⌘/Ctrl</kbd>
          <kbd className="ml-1 rounded border border-border bg-muted px-1">Enter</kbd> to run. Up to
          500 rows.
        </p>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        <LiveTableView
          table={tableForResult}
          loading={loading}
          error={error}
          emptyHint="Pick a table on the left, or write a query above and run it."
        />
      </div>

      <SaveAsReportDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        sql={sql}
        result={result}
        defaultTitle={defaultReportTitle}
        onCreated={handleSaved}
      />
    </div>
  )
}
