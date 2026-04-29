import { useCallback, useState } from 'react'
import { Check, ChevronRight, Copy, Play } from 'lucide-react'
import { toast } from 'sonner'

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

interface SqlEditorPanelProps {
  sql: string
  onSqlChange: (next: string) => void
  onRun: () => void
  running: boolean
  error: string | null
  locked?: boolean
  defaultOpen?: boolean
  runLabel?: string
  runningLabel?: string
  collapsedHint?: string
  placeholder?: string
}

/** The Collapsible SQL editor used by both `TableChatView` (existing
 *  Reports) and `EmptyReportScreen` (brand-new Reports seeded by SQL).
 *  Owns only its own open/copied UI state — the SQL value, run handler,
 *  and error string are controlled by the parent. */
export function SqlEditorPanel({
  sql,
  onSqlChange,
  onRun,
  running,
  error,
  locked = false,
  defaultOpen = true,
  runLabel = 'Run',
  runningLabel = 'Running',
  collapsedHint,
  placeholder = 'SELECT ...',
}: SqlEditorPanelProps) {
  const [open, setOpen] = useState(defaultOpen)
  const [copied, setCopied] = useState(false)

  const trimmed = sql.trim()
  const canRun = !running && !locked && !!trimmed

  const copySql = useCallback(async () => {
    if (!trimmed) return
    try {
      await navigator.clipboard.writeText(trimmed)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy — your browser blocked clipboard access')
    }
  }, [trimmed])

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
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
          {!open && trimmed ? (
            <span className="truncate font-mono text-2xs text-muted-foreground/80">
              {trimmed.split('\n')[0].slice(0, 120)}
              {trimmed.split('\n').length > 1 || trimmed.length > 120 ? ' …' : ''}
            </span>
          ) : !open && collapsedHint ? (
            <span className="truncate text-2xs text-muted-foreground/80">
              {collapsedHint}
            </span>
          ) : null}
        </CollapsibleTrigger>
        <button
          type="button"
          onClick={onRun}
          disabled={!canRun}
          aria-label={`${runLabel} SQL`}
          title={
            locked
              ? 'Unlock the table to edit and run SQL'
              : `${runLabel} SQL (⌘/Ctrl+Enter)`
          }
          className={cn(
            'flex shrink-0 items-center gap-1 px-3 text-2xs text-muted-foreground transition-colors',
            'hover:bg-muted/40 hover:text-foreground disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-muted-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
          )}
        >
          <Play className="size-3.5" />
          <span className="font-medium uppercase tracking-[0.14em]">
            {running ? runningLabel : runLabel}
          </span>
        </button>
        <button
          type="button"
          onClick={copySql}
          disabled={!trimmed}
          aria-label="Copy SQL"
          title={copied ? 'Copied' : 'Copy SQL'}
          className={cn(
            'flex shrink-0 items-center gap-1 px-3 text-2xs text-muted-foreground transition-colors',
            'hover:bg-muted/40 hover:text-foreground disabled:opacity-50',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
          )}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          <span className="font-medium uppercase tracking-[0.14em]">
            {copied ? 'Copied' : 'Copy'}
          </span>
        </button>
      </div>
      <CollapsibleContent className="border-t border-border bg-background/50">
        <Textarea
          className="min-h-[120px] max-h-[18rem] resize-y overflow-auto rounded-none border-0 bg-transparent font-mono text-xs leading-relaxed [field-sizing:fixed] focus-visible:ring-0 focus-visible:ring-offset-0"
          spellCheck={false}
          value={sql}
          onChange={(e) => onSqlChange(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              if (canRun) onRun()
            }
          }}
          readOnly={locked}
          placeholder={placeholder}
        />
        <p className="border-t border-border px-3 py-1.5 text-2xs text-muted-foreground">
          {locked ? (
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
        {error ? (
          <p className="border-t border-border bg-destructive/10 px-3 py-1.5 text-2xs text-destructive">
            {error}
          </p>
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  )
}
