import { ChevronRight, Database, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { ToolRunRecord } from '@/lib/types'

interface ToolCallCardProps {
  run: ToolRunRecord
}

function summarizeInput(toolName: string, input: Record<string, unknown>): string {
  if (toolName === 'run_sql' && typeof input.sql === 'string') {
    const sql = (input.sql as string).replace(/\s+/g, ' ').trim()
    return sql.length > 90 ? `${sql.slice(0, 87)}…` : sql
  }
  // Generic: inline up to two key/value pairs
  const entries = Object.entries(input).slice(0, 2)
  if (entries.length === 0) return ''
  return entries
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ')
}

function statusMeta(status: string) {
  switch (status) {
    case 'pending':
    case 'running':
      return {
        icon: <Loader2 className="size-3.5 animate-spin text-muted-foreground" />,
        label: 'Running',
      }
    case 'error':
      return {
        icon: <AlertCircle className="size-3.5 text-destructive" />,
        label: 'Error',
      }
    case 'completed':
    default:
      return {
        icon: <CheckCircle2 className="size-3.5 text-accent" />,
        label: 'Completed',
      }
  }
}

export function ToolCallCard({ run }: ToolCallCardProps) {
  const { icon, label } = statusMeta(run.status)
  const summary = summarizeInput(run.tool_name, run.input)

  return (
    <Collapsible className="my-3 overflow-hidden rounded-lg border border-border bg-card">
      <CollapsibleTrigger
        className={cn(
          'group flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/40',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
        )}
      >
        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-90" />
        <Database className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs font-medium text-foreground">{run.tool_name}</span>
        {summary ? (
          <span className="truncate font-mono text-xs text-muted-foreground">{summary}</span>
        ) : null}
        <div className="ml-auto flex shrink-0 items-center gap-1.5 text-2xs text-muted-foreground">
          {icon}
          <span>{label}</span>
          {typeof run.duration_ms === 'number' ? (
            <>
              <span aria-hidden>·</span>
              <span className="tabular">{run.duration_ms}ms</span>
            </>
          ) : null}
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-border bg-muted/20">
        <div className="space-y-3 px-4 py-3">
          <ToolPayload label="Input" value={JSON.stringify(run.input, null, 2)} />
          {run.result ? <ToolPayload label="Result" value={prettyJson(run.result)} /> : null}
          {run.error ? <ToolPayload label="Error" value={run.error} variant="error" /> : null}
          {run.hint ? <ToolPayload label="Hint" value={run.hint} variant="muted" /> : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolPayload({
  label,
  value,
  variant = 'default',
}: {
  label: string
  value: string
  variant?: 'default' | 'error' | 'muted'
}) {
  return (
    <div className="space-y-1">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <pre
        className={cn(
          'overflow-x-auto whitespace-pre-wrap rounded-md bg-background px-3 py-2 font-mono text-xs leading-relaxed',
          variant === 'error' && 'text-destructive',
          variant === 'muted' && 'text-muted-foreground',
        )}
      >
        {value}
      </pre>
    </div>
  )
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}
