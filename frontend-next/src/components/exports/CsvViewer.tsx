import { useEffect, useState } from 'react'
import { Download, MessageSquarePlus, X } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { apiGet, apiPost, ApiError } from '@/lib/api'
import { relativeTime } from '@/lib/datetime'
import { formatBytes, type ExportDetail, type NewSessionFromExportResponse } from '@/lib/exports'

interface CsvViewerProps {
  exportId: string
  onClose: () => void
  onConversationCreated: (conversationId: string) => void
}

export function CsvViewer({ exportId, onClose, onConversationCreated }: CsvViewerProps) {
  const [detail, setDetail] = useState<ExportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
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
    <div className="flex flex-1 flex-col overflow-hidden">
      <header className="flex shrink-0 items-start gap-3 border-b border-border px-6 py-4">
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
            CSV
          </p>
          {detail ? (
            <>
              <h1 className="font-display text-2xl font-medium tracking-tight">
                {detail.title}
              </h1>
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
            <div className="border-b border-border bg-muted/20 px-6 py-3">
              <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground mb-1">
                Source SQL
              </p>
              <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-foreground">
                {detail.sql}
              </pre>
            </div>
          ) : null}

          <ScrollArea className="flex-1">
            <div className="overflow-x-auto">
              <table className="w-full border-separate border-spacing-0 text-sm">
                <thead className="sticky top-0 z-10 bg-background">
                  <tr>
                    {detail.columns.map((col) => (
                      <th
                        key={col}
                        className="border-b border-border px-4 py-2.5 text-left font-medium text-muted-foreground first:pl-6 last:pr-6"
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.preview_rows.map((row, i) => (
                    <tr key={i} className="hover:bg-muted/40">
                      {detail.columns.map((col) => (
                        <td
                          key={col}
                          className="border-t border-border px-4 py-2 first:pl-6 last:pr-6"
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
          </ScrollArea>
        </>
      )}
    </div>
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
  if (typeof value === 'string') return <span>{value}</span>
  return <span className="font-mono text-xs">{JSON.stringify(value)}</span>
}
