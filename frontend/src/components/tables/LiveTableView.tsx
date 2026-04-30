import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, Search, X } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { cellTitle, cleanNumericString, compareValues } from '@/lib/csv'
import type { TableState } from '@/lib/tables'

interface LiveTableViewProps {
  table: TableState | null
  loading: boolean
  error: string | null
  /** Shown when there is no table yet — guides the user toward "Change table". */
  emptyHint?: string
}

type SortDirection = 'asc' | 'desc'
type SortState = { column: string | null; direction: SortDirection }

function rowMatchesQuery(row: Record<string, unknown>, columns: string[], q: string): boolean {
  for (const col of columns) {
    const v = row[col]
    if (v == null) continue
    const s = typeof v === 'string' ? v : String(v)
    if (s.toLowerCase().includes(q)) return true
  }
  return false
}

export function LiveTableView({
  table,
  loading,
  error,
  emptyHint = 'Ask the agent to build one.',
}: LiveTableViewProps) {
  const [sort, setSort] = useState<SortState>({ column: null, direction: 'asc' })
  const [search, setSearch] = useState('')

  // Filter first so sort operates on the visible subset.
  const filteredRows = useMemo(() => {
    if (!table) return []
    const q = search.trim().toLowerCase()
    if (!q) return table.rows
    return table.rows.filter((r) => rowMatchesQuery(r, table.columns, q))
  }, [table, search])

  const sortedRows = useMemo(() => {
    if (!sort.column) return filteredRows
    const col = sort.column
    const dir = sort.direction === 'asc' ? 1 : -1
    return [...filteredRows].sort((a, b) => dir * compareValues(a[col], b[col]))
  }, [filteredRows, sort])

  const onHeaderClick = (col: string) => {
    setSort((prev) =>
      prev.column === col
        ? { column: col, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { column: col, direction: 'asc' },
    )
  }

  if (error) {
    return (
      <div className="grid flex-1 place-items-center px-6 text-sm text-destructive">{error}</div>
    )
  }

  if (loading && !table) {
    return (
      <div className="space-y-3 p-6">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
    )
  }

  if (!table || table.rows.length === 0) {
    return (
      <div className="grid flex-1 place-items-center px-6 text-center">
        <div className="max-w-md space-y-2">
          <p className="text-base text-muted-foreground">No table yet.</p>
          <p className="text-sm text-muted-foreground/80">{emptyHint}</p>
        </div>
      </div>
    )
  }

  const totalRows = table.rows.length
  const filteredCount = filteredRows.length
  const trimmedSearch = search.trim()

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-6 py-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            placeholder="Search rows…"
            className="h-8 pl-8 pr-8 text-sm"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search ? (
            <button
              type="button"
              onClick={() => setSearch('')}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>
        {trimmedSearch ? (
          <p className="shrink-0 text-2xs text-muted-foreground">
            {filteredCount.toLocaleString()} of {totalRows.toLocaleString()}
          </p>
        ) : null}
      </div>
      <div className="min-h-0 overflow-auto">
        {trimmedSearch && filteredCount === 0 ? (
          <div className="grid h-full place-items-center px-6 text-sm text-muted-foreground">
            No rows match "{trimmedSearch}"
          </div>
        ) : (
        <table className="min-w-full border-separate border-spacing-0 text-sm">
          <thead className="sticky top-0 z-10 bg-card">
            <tr>
              {table.columns.map((col) => {
                const active = sort.column === col
                return (
                  <th
                    key={col}
                    className="border-b border-border bg-card p-0 text-left font-medium text-muted-foreground first:pl-2 last:pr-2"
                  >
                    <button
                      type="button"
                      onClick={() => onHeaderClick(col)}
                      className={cn(
                        'group flex w-full items-center gap-1.5 px-4 py-2.5 text-left transition-colors',
                        'hover:text-foreground focus-visible:outline-none focus-visible:bg-muted/40',
                        active && 'text-foreground',
                      )}
                    >
                      <span className="truncate">{col}</span>
                      <SortIcon active={active} direction={sort.direction} />
                    </button>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, i) => (
              <tr key={i} className="hover:bg-muted/40">
                {table.columns.map((col) => (
                  <td
                    key={col}
                    className="max-w-[40ch] truncate whitespace-nowrap border-t border-border px-4 py-2 first:pl-6 last:pr-6"
                    title={cellTitle(row[col])}
                  >
                    <CellValue value={row[col]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        )}
        {table.truncated ? (
          <p className="px-6 py-3 text-2xs text-muted-foreground">
            Result was capped at {table.row_count.toLocaleString()} rows. Increase the size dropdown
            to fit more, or ask the agent to refine the query.
          </p>
        ) : null}
      </div>
    </div>
  )
}

function SortIcon({ active, direction }: { active: boolean; direction: SortDirection }) {
  if (!active) {
    return (
      <ArrowUpDown className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
    )
  }
  return direction === 'asc' ? (
    <ArrowUp className="size-3 shrink-0 text-foreground" />
  ) : (
    <ArrowDown className="size-3 shrink-0 text-foreground" />
  )
}

function CellValue({ value }: { value: unknown }) {
  if (value == null) return <span className="text-muted-foreground/60">—</span>
  if (typeof value === 'number') {
    return <span className="tabular text-right block">{value.toLocaleString()}</span>
  }
  if (typeof value === 'boolean') {
    return <span className="text-muted-foreground">{value ? 'true' : 'false'}</span>
  }
  if (typeof value === 'string') return <span>{cleanNumericString(value)}</span>
  return <span className="font-mono text-xs">{JSON.stringify(value)}</span>
}
