import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronRight,
  Download,
  MessageSquarePlus,
  Pencil,
  X,
} from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { relativeTime } from '@/lib/datetime'
import { useExportsContext } from '@/lib/exportsContext'
import { formatBytes, type ExportDetail, type NewSessionFromExportResponse } from '@/lib/exports'

interface CsvViewerProps {
  exportId: string
  onClose: () => void
  onConversationCreated: (conversationId: string) => void
}

type SortDirection = 'asc' | 'desc'
type SortState = { column: string | null; direction: SortDirection }

export function CsvViewer({ exportId, onClose, onConversationCreated }: CsvViewerProps) {
  const exportsCtx = useExportsContext()
  const [detail, setDetail] = useState<ExportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [sort, setSort] = useState<SortState>({ column: null, direction: 'asc' })
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renaming, setRenaming] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setSort({ column: null, direction: 'asc' })
    apiGet<ExportDetail>(`/chat/exports/${encodeURIComponent(exportId)}`)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
        setLoading(false)
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof ApiError ? e.detail : 'Could not load CSV')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [exportId])

  const sortedRows = useMemo(() => {
    if (!detail) return []
    if (!sort.column) return detail.preview_rows
    const col = sort.column
    const dir = sort.direction === 'asc' ? 1 : -1
    return [...detail.preview_rows].sort((a, b) => dir * compareValues(a[col], b[col]))
  }, [detail, sort])

  const onHeaderClick = (col: string) => {
    setSort((prev) =>
      prev.column === col
        ? { column: col, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { column: col, direction: 'asc' },
    )
  }

  const startRename = () => {
    if (!detail) return
    setRenameValue(detail.title)
    setRenameOpen(true)
  }

  const submitRename = async () => {
    if (!detail) return
    const next = renameValue.trim()
    if (!next || next === detail.title) {
      setRenameOpen(false)
      return
    }
    setRenaming(true)
    try {
      await exportsCtx.rename(detail.id, next)
      setDetail({ ...detail, title: next })
      toast.success('Renamed')
      setRenameOpen(false)
    } catch {
      toast.error('Could not rename')
    } finally {
      setRenaming(false)
    }
  }

  const newSession = async () => {
    setCreating(true)
    try {
      const res = await apiPost<NewSessionFromExportResponse>(
        `/chat/exports/${encodeURIComponent(exportId)}/new-session`,
        {},
      )
      onConversationCreated(res.conversation_id)
      toast.success('New conversation started from this CSV')
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not start conversation'
      toast.error(msg)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-6">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <header className="flex shrink-0 items-start gap-3 border-b border-border px-6 py-4">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
            CSV
          </p>
          {detail ? (
            <>
              <div className="flex items-center gap-2">
                <h1 className="font-display text-2xl font-medium tracking-tight">
                  {detail.title}
                </h1>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  onClick={startRename}
                  aria-label="Rename CSV"
                >
                  <Pencil className="size-4" />
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-muted-foreground">
                <span className="tabular">{detail.row_count.toLocaleString()} rows</span>
                <span aria-hidden>·</span>
                <span className="tabular">{detail.columns.length} columns</span>
                <span aria-hidden>·</span>
                <span className="tabular">{formatBytes(detail.file_size)}</span>
                <span aria-hidden>·</span>
                <span className="tabular">{relativeTime(detail.created_at)}</span>
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-7 w-72" />
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {detail ? (
            <>
              <Button variant="secondary" size="sm" asChild>
                <a href={detail.download_url} download>
                  <Download className="size-4" />
                  Download
                </a>
              </Button>
              <Button size="sm" onClick={newSession} disabled={creating}>
                <MessageSquarePlus className="size-4" />
                New chat with this CSV
              </Button>
            </>
          ) : null}
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </Button>
        </div>
      </header>

      {error ? (
        <div className="grid flex-1 place-items-center px-6 text-sm text-destructive">{error}</div>
      ) : loading || !detail ? (
        <div className="space-y-3 p-6">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : (
        <>
          {detail.sql ? (
            <div className="shrink-0 border-b border-border px-6 py-3">
              <Collapsible
                defaultOpen={false}
                className="overflow-hidden rounded-lg border border-border bg-muted/20"
              >
                <CollapsibleTrigger
                  className={cn(
                    'group flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/40',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                  )}
                >
                  <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-90" />
                  <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                    Source SQL
                  </span>
                </CollapsibleTrigger>
                <CollapsibleContent className="border-t border-border bg-background/50">
                  <pre className="overflow-x-auto whitespace-pre-wrap px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
                    {detail.sql}
                  </pre>
                </CollapsibleContent>
              </Collapsible>
            </div>
          ) : null}

          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex-1 min-h-0 overflow-auto">
              <table className="min-w-full border-separate border-spacing-0 text-sm">
                <thead className="sticky top-0 z-10 bg-card">
                  <tr>
                    {detail.columns.map((col) => {
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
                      {detail.columns.map((col) => (
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
              {detail.preview_truncated ? (
                <p className="px-6 py-3 text-2xs text-muted-foreground">
                  Showing the first {detail.preview_rows.length.toLocaleString()} rows.
                  Download the full file for the rest.
                </p>
              ) : null}
            </div>
          </div>
        </>
      )}
      </div>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename CSV</DialogTitle>
            <DialogDescription>Give this CSV a clearer title.</DialogDescription>
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
            placeholder="CSV title"
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameOpen(false)} disabled={renaming}>
              Cancel
            </Button>
            <Button
              onClick={submitRename}
              disabled={renaming || !renameValue.trim() || renameValue.trim() === detail?.title}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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

function compareValues(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1

  const an = toNumber(a)
  const bn = toNumber(b)
  if (an !== null && bn !== null) return an - bn

  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
}

function toNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    if (Number.isFinite(n)) return n
  }
  return null
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

// CSV rows come back as strings. Floats round-tripped through Python end up
// with precision artifacts like "471.20000000000005" — round to 6 decimals
// (sports stats never need more) and strip trailing zeros so they read
// cleanly. Integer strings and non-numeric strings pass through unchanged.
function cleanNumericString(s: string): string {
  if (!s.includes('.')) return s
  const n = Number(s)
  if (!Number.isFinite(n)) return s
  return Number(n.toFixed(6)).toString()
}

function cellTitle(value: unknown): string | undefined {
  if (value == null) return undefined
  if (typeof value === 'string') return value.length > 0 ? value : undefined
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
