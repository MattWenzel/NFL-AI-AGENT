import { ChevronRight, Database, AlertCircle, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Markdown } from '@/components/thread/Markdown'
import { ToolPayload, prettyJson } from '@/components/thread/ToolPayload'
import { cn } from '@/lib/utils'
import type {
  AssistantPartRecord,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

interface AgentResponseProps {
  /** All consecutive assistant turns that make up one logical agent response. */
  turns: TurnRecord[]
  /** All AssistantPartRecords across those turns, ordered by turn → order_index. */
  parts: AssistantPartRecord[]
  /** All ToolRunRecords across those turns, in chronological order. */
  toolRuns: ToolRunRecord[]
  /** True when the inspector is currently scoped to this exchange. */
  selected?: boolean
  /** Click anywhere on the response to scope the inspector to this exchange. */
  onSelect?: () => void
}

/**
 * Renders one logical agent response — possibly spanning multiple backend
 * turns (one per agent-loop iteration). Tool calls are grouped under a
 * single collapsible "Thinking" disclosure at the top, matching the
 * vanilla frontend's pattern. Text parts render as Markdown beneath.
 */
export function AgentResponse({
  turns,
  parts,
  toolRuns,
  selected = false,
  onSelect,
}: AgentResponseProps) {
  // Streaming if ANY of the turns is still active.
  const isStreaming = turns.some((t) => t.status === 'pending' || t.status === 'streaming')

  // Concatenate ordered text content (parts already merged within a turn by
  // the chatStore; here we also concatenate across iterations).
  const textContent = parts
    .filter((p) => p.kind === 'text')
    .map((p) => p.content)
    .join('\n')
    .trim()

  // Reasoning / thinking parts that the agent emits between tool calls.
  const reasoningParts = parts.filter((p) => p.kind === 'thinking' || p.kind === 'reasoning')

  // Last error — if any turn errored, surface the most recent message.
  const lastError = [...turns].reverse().find((t) => t.error)?.error ?? null

  // Token totals across all iterations.
  const totalIn = turns.reduce((s, t) => s + (t.input_tokens || 0), 0)
  const totalOut = turns.reduce((s, t) => s + (t.output_tokens || 0), 0)
  const showTokens =
    !isStreaming && turns.some((t) => t.status === 'complete') && (totalIn > 0 || totalOut > 0)

  const hasAny =
    toolRuns.length > 0 || textContent.length > 0 || reasoningParts.length > 0 || isStreaming

  if (!hasAny) {
    // Truly nothing to show — collapse the whole block.
    return null
  }

  return (
    <div
      role={onSelect ? 'button' : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onClick={(e) => {
        if (!onSelect) return
        e.stopPropagation()
        onSelect()
      }}
      onKeyDown={(e) => {
        if (!onSelect) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        '-mx-3 space-y-2 rounded-xl px-3 py-2 transition-colors',
        onSelect && 'cursor-pointer hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected && 'bg-muted/30 ring-1 ring-accent/40',
      )}
    >
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Agent
      </p>

      {toolRuns.length > 0 ? <ThinkingBlock runs={toolRuns} /> : null}

      {reasoningParts.length > 0 ? (
        <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer text-2xs font-medium uppercase tracking-[0.14em] hover:text-foreground">
            Reasoning
          </summary>
          <div className="mt-1 space-y-2">
            {reasoningParts.map((p) => (
              <p key={p.id} className="whitespace-pre-wrap leading-relaxed">
                {p.content}
              </p>
            ))}
          </div>
        </details>
      ) : null}

      {textContent ? <Markdown source={textContent} /> : null}

      {isStreaming && !textContent && toolRuns.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          <span>Thinking…</span>
        </p>
      ) : null}

      {lastError ? (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{lastError}</span>
        </div>
      ) : null}

      {showTokens ? (
        <p className="pt-1 text-2xs text-muted-foreground tabular">
          {totalIn.toLocaleString()} in · {totalOut.toLocaleString()} out
        </p>
      ) : null}
    </div>
  )
}

function ThinkingBlock({ runs }: { runs: ToolRunRecord[] }) {
  const totalMs = runs.reduce((s, r) => s + (r.duration_ms ?? 0), 0)
  const errors = runs.filter((r) => r.status === 'error').length
  const running = runs.some((r) => r.status === 'pending' || r.status === 'running')
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
          {totalMs > 0 ? (
            <>
              <span aria-hidden>·</span>
              <span className="tabular">{totalMs}ms</span>
            </>
          ) : null}
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

function ToolRunRow({ run }: { run: ToolRunRecord }) {
  const summary = summarizeInput(run.tool_name, run.input)
  // Only execute_sql carries payloads worth surfacing inline — guides, schemas,
  // and other helpers are pure prep work the user shouldn't have to read.
  const showInlineDetails =
    run.tool_name === 'execute_sql' && (!!run.result || !!run.error || !!run.hint)
  return (
    <li className="px-3 py-2">
      <div className="flex items-center gap-2">
        <Database className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs font-medium text-foreground">{run.tool_name}</span>
        {summary ? (
          <span className="truncate font-mono text-xs text-muted-foreground">{summary}</span>
        ) : null}
        <div className="ml-auto flex shrink-0 items-center gap-1.5 text-2xs text-muted-foreground">
          <StatusDot status={run.status} />
          {typeof run.duration_ms === 'number' ? (
            <span className="tabular">{run.duration_ms}ms</span>
          ) : null}
        </div>
      </div>
      {showInlineDetails ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-2xs text-muted-foreground hover:text-foreground">
            Show details
          </summary>
          <div className="mt-2 space-y-2">
            <ToolPayload label="Input" value={JSON.stringify(run.input, null, 2)} />
            {run.result ? <ToolPayload label="Result" value={prettyJson(run.result)} /> : null}
            {run.error ? <ToolPayload label="Error" value={run.error} variant="error" /> : null}
            {run.hint ? <ToolPayload label="Hint" value={run.hint} variant="muted" /> : null}
          </div>
        </details>
      ) : null}
    </li>
  )
}

function StatusDot({ status }: { status: string }) {
  if (status === 'pending' || status === 'running') {
    return <Loader2 className="size-3 animate-spin" />
  }
  if (status === 'error') {
    return <AlertCircle className="size-3 text-destructive" />
  }
  return <CheckCircle2 className="size-3 text-accent" />
}

function summarizeInput(toolName: string, input: Record<string, unknown>): string {
  if (toolName === 'execute_sql' || toolName === 'run_sql') {
    if (typeof input.sql === 'string') {
      const sql = (input.sql as string).replace(/\s+/g, ' ').trim()
      return sql.length > 90 ? `${sql.slice(0, 87)}…` : sql
    }
  }
  const entries = Object.entries(input).slice(0, 2)
  if (entries.length === 0) return ''
  return entries
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ')
}

