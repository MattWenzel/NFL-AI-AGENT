import { useMemo, useRef } from 'react'
import { AlertCircle, CheckCircle2, ChevronRight, Loader2 } from 'lucide-react'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { HelperMessage, HelperToolRun } from '@/lib/state/dbHelperChat'
import { useScrollToBottom } from '@/lib/useScrollToBottom'
import { cn } from '@/lib/utils'

interface HelperMessageListProps {
  messages: HelperMessage[]
  streaming: boolean
  error: string | null
}

/** Slim message renderer for the SQL helper. Visually mirrors the regular
 *  chat — a single "Thinking" disclosure groups the tool calls; click any
 *  row to expand its input + result inline. The helper has no inspector,
 *  so detail-view stays inside this list. */
export function HelperMessageList({ messages, streaming, error }: HelperMessageListProps) {
  const tailRef = useRef<HTMLDivElement | null>(null)
  // Trigger the scroll on user-message count rather than the full
  // message list, so streaming text deltas don't re-fire on every chunk.
  const userMessageCount = useMemo(
    () => messages.filter((m) => m.role === 'user').length,
    [messages],
  )
  useScrollToBottom({
    trackedKey: userMessageCount > 0 ? `user-${userMessageCount}` : null,
    // The helper is ephemeral — no session id to anchor on. Passing a
    // stable string keeps `isFreshLoad` false after the first send so
    // every scroll uses the smooth animation.
    resetKey: 'helper',
    getElement: () => tailRef.current,
    block: 'end',
  })

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
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-6 lg:px-10">
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
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-2xl rounded-2xl rounded-tr-md bg-secondary px-4 py-2.5 text-secondary-foreground">
        <p className="whitespace-pre-wrap text-sm leading-snug">{text}</p>
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
    <div className="-mx-3 flex flex-col gap-2 rounded-xl px-3 py-2">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Agent
      </p>
      {toolRuns.length > 0 ? <ToolRunGroup runs={toolRuns} /> : null}
      {text ? (
        <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{text}</div>
      ) : null}
      {streaming && !hasContent ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          <span>Thinking…</span>
        </p>
      ) : null}
    </div>
  )
}

function ToolRunGroup({ runs }: { runs: HelperToolRun[] }) {
  const errors = runs.filter((r) => r.status === 'failed').length
  const running = runs.some((r) => r.status === 'pending')
  const label = `${runs.length} ${runs.length === 1 ? 'tool call' : 'tool calls'}`

  return (
    <Collapsible className="overflow-hidden rounded-lg border border-border bg-muted/20">
      <CollapsibleTrigger
        onClick={(e) => e.stopPropagation()}
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
            <ToolRunRow key={run.id} run={run} />
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolRunRow({ run }: { run: HelperToolRun }) {
  const inputJson = stringifyJson(run.input)
  const inputSummary = summarize(inputJson)
  return (
    <li>
      <details className="group">
        <summary
          className={cn(
            'flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
          )}
        >
          <StatusDot status={run.status} />
          <span className="font-mono text-xs font-medium text-foreground">{run.name}</span>
          {inputSummary ? (
            <span className="truncate font-mono text-xs text-muted-foreground">{inputSummary}</span>
          ) : null}
          <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground transition-transform duration-150 group-open:rotate-90" />
        </summary>
        <div className="flex flex-col gap-2 border-t border-border bg-background/40 px-3 py-2 text-xs">
          {inputJson ? (
            <div>
              <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Input
              </p>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-foreground">
                {inputJson}
              </pre>
            </div>
          ) : null}
          {run.error ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
              {run.error}
            </div>
          ) : run.content ? (
            <div>
              <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Result
              </p>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-foreground">
                {run.content}
              </pre>
            </div>
          ) : null}
        </div>
      </details>
    </li>
  )
}

function StatusDot({ status }: { status: HelperToolRun['status'] }) {
  if (status === 'pending') {
    return <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground" />
  }
  if (status === 'failed') {
    return <span className="size-2 shrink-0 rounded-full bg-destructive" aria-hidden />
  }
  return <span className="size-2 shrink-0 rounded-full bg-accent" aria-hidden />
}

function stringifyJson(value: Record<string, unknown> | null | undefined): string {
  if (!value) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return ''
  }
}

function summarize(s: string | undefined | null): string {
  if (!s) return ''
  const trimmed = s.trim().replace(/\s+/g, ' ')
  return trimmed.length > 80 ? `${trimmed.slice(0, 77)}…` : trimmed
}
