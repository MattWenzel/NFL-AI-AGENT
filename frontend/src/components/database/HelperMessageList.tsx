import { useEffect, useRef } from 'react'
import { AlertCircle, CheckCircle2, ChevronRight, Loader2 } from 'lucide-react'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { HelperMessage, HelperToolRun } from '@/lib/dbHelperChat'

interface HelperMessageListProps {
  messages: HelperMessage[]
  streaming: boolean
  error: string | null
}

/** Slim message renderer for the SQL helper panel.
 *  No exchange grouping, no inspector deep-links, no markdown — keep it
 *  small and predictable. Tool calls roll up under a single "Thinking"
 *  collapsible per assistant turn (matching the main chat); each row is
 *  itself expandable to inspect the tool's input + result inline. */
export function HelperMessageList({ messages, streaming, error }: HelperMessageListProps) {
  // Auto-scroll to the bottom on new content, mirroring the regular Thread.
  const tailRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    tailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, streaming])

  if (messages.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 pb-8 text-center">
        <p className="text-xs text-muted-foreground/80">
          Ask about the schema, plan a query, or paste SQL for feedback.
        </p>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 text-sm">
      {messages.map((m, i) =>
        m.role === 'user' ? (
          <UserBubble key={i} text={m.text} />
        ) : (
          <AssistantBubble
            key={i}
            text={m.text}
            toolRuns={m.toolRuns ?? []}
            streaming={streaming && i === messages.length - 1}
          />
        ),
      )}
      {error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}
      <div ref={tailRef} />
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-secondary px-3 py-2 text-foreground">
        <p className="whitespace-pre-wrap break-words">{text}</p>
      </div>
    </div>
  )
}

function AssistantBubble({
  text,
  toolRuns,
  streaming,
}: {
  text: string
  toolRuns: HelperToolRun[]
  streaming: boolean
}) {
  const hasContent = text.length > 0 || toolRuns.length > 0
  return (
    <div className="space-y-2">
      {toolRuns.length > 0 ? <ThinkingBlock runs={toolRuns} /> : null}
      {text ? (
        <div className="whitespace-pre-wrap break-words text-foreground">{text}</div>
      ) : null}
      {streaming && !hasContent ? (
        <div className="text-xs text-muted-foreground">Thinking…</div>
      ) : null}
      {streaming && hasContent ? (
        <div className="text-2xs text-muted-foreground">Streaming…</div>
      ) : null}
    </div>
  )
}

function ThinkingBlock({ runs }: { runs: HelperToolRun[] }) {
  const running = runs.some((r) => r.status === 'pending')
  const errors = runs.filter((r) => r.status === 'failed').length
  const label = `${runs.length} ${runs.length === 1 ? 'tool call' : 'tool calls'}`

  return (
    <Collapsible className="overflow-hidden rounded-lg border border-border bg-muted/20">
      <CollapsibleTrigger
        className={cn(
          'group flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/40',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
        )}
      >
        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-90" />
        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Thinking
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1.5 text-2xs text-muted-foreground">
          {running ? (
            <Loader2 className="size-3 animate-spin" />
          ) : errors > 0 ? (
            <AlertCircle className="size-3 text-destructive" />
          ) : (
            <CheckCircle2 className="size-3 text-accent" />
          )}
          <span className="tabular">{label}</span>
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-border bg-background/50">
        <ul className="divide-y divide-border">
          {runs.map((run) => (
            <li key={run.id}>
              <ToolRunRow run={run} />
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolRunRow({ run }: { run: HelperToolRun }) {
  const statusLabel =
    run.status === 'pending'
      ? 'running'
      : run.status === 'failed'
        ? 'failed'
        : 'completed'
  const summary = (() => {
    const sql = typeof run.input.sql === 'string' ? run.input.sql : null
    if (sql) return sql.length > 60 ? sql.slice(0, 57) + '…' : sql
    if (run.input.topic) return String(run.input.topic)
    if (run.input.table_name) return String(run.input.table_name)
    if (run.input.query) return String(run.input.query).slice(0, 60)
    if (run.input.player_gsis_id) return String(run.input.player_gsis_id)
    return ''
  })()

  return (
    <details className="group/run text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 transition-colors hover:bg-muted/40">
        <span className="font-mono font-medium text-foreground">{run.name}</span>
        {summary ? (
          <span className="truncate text-muted-foreground">{summary}</span>
        ) : null}
        <span
          className={cn(
            'ml-auto shrink-0 text-2xs uppercase tracking-wide',
            run.status === 'failed'
              ? 'text-destructive'
              : run.status === 'pending'
                ? 'text-muted-foreground'
                : 'text-muted-foreground/80',
          )}
        >
          {statusLabel}
        </span>
      </summary>
      <div className="space-y-2 border-t border-border bg-muted/20 px-3 py-2">
        <div>
          <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            Input
          </p>
          <pre className="overflow-x-auto rounded bg-background px-2 py-1.5 font-mono text-2xs text-foreground">
            {JSON.stringify(run.input, null, 2)}
          </pre>
        </div>
        {run.content !== null ? (
          <div>
            <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
              {run.status === 'failed' ? 'Error' : 'Result'}
            </p>
            <pre className="max-h-64 overflow-auto rounded bg-background px-2 py-1.5 font-mono text-2xs text-foreground">
              {prettyContent(run.content)}
            </pre>
          </div>
        ) : null}
        {run.error ? (
          <p className="text-2xs text-destructive">{run.error}</p>
        ) : null}
      </div>
    </details>
  )
}

function prettyContent(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}
