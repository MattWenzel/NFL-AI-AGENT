import { ArrowLeft, ChevronLeft, ChevronRight, Database } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ToolPayload, prettyJson } from '@/components/thread/ToolPayload'
import { useChatContext } from '@/lib/chatContext'
import { relativeTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { AssistantPartRecord, ConversationTranscript, ToolRunRecord, TurnRecord } from '@/lib/types'

export function Inspector() {
  const chat = useChatContext()
  const transcript = chat.transcript

  if (!transcript || transcript.turns.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto space-y-6 p-5">
        <Section label="Session">
          <p className="text-sm text-muted-foreground">
            No conversation selected. Open a chat or start a new one to inspect runtime details.
          </p>
        </Section>
      </div>
    )
  }

  if (chat.selectedToolRunId) {
    const run = transcript.tool_runs.find((r) => r.id === chat.selectedToolRunId)
    if (run) {
      return (
        <div className="flex min-h-0 flex-1 flex-col p-5">
          <ToolRunDetail
            run={run}
            transcript={transcript}
            onClear={() => chat.selectToolRun(null)}
            onSelectRun={(id) => chat.selectToolRun(id, exchangeIdForTurn(transcript, run.turn_id))}
          />
        </div>
      )
    }
  }

  if (chat.selectedExchangeId) {
    const slice = sliceForExchange(transcript, chat.selectedExchangeId)
    if (slice) {
      return (
        <div className="flex-1 overflow-y-auto space-y-7 p-5">
          <ExchangeMeta
            slice={slice}
            transcript={transcript}
            onClear={() => chat.selectExchange(null)}
          />
          <ExchangeToolRuns slice={slice} transcript={transcript} />
        </div>
      )
    }
  }

  return (
    <div className="flex-1 overflow-y-auto space-y-7 p-5">
      <SessionMeta transcript={transcript} />
      <ToolRuns transcript={transcript} />
      <CompactionHistory transcript={transcript} />
    </div>
  )
}

interface ExchangeSlice {
  userTurn: TurnRecord | null
  assistantTurns: TurnRecord[]
  parts: AssistantPartRecord[]
  toolRuns: ToolRunRecord[]
}

function sliceForExchange(
  transcript: ConversationTranscript,
  exchangeId: string,
): ExchangeSlice | null {
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)
  const idx = visibleTurns.findIndex((t) => t.id === exchangeId)
  if (idx < 0) return null
  const anchor = visibleTurns[idx]

  let userTurn: TurnRecord | null = null
  let firstAgentIdx: number
  if (anchor.role === 'user') {
    userTurn = anchor
    firstAgentIdx = idx + 1
  } else {
    // Walk back to the user turn that triggered this assistant block.
    firstAgentIdx = idx
    for (let i = idx - 1; i >= 0; i--) {
      if (visibleTurns[i].role === 'user') {
        userTurn = visibleTurns[i]
        break
      }
      firstAgentIdx = i
    }
  }

  const assistantTurns: TurnRecord[] = []
  for (let i = firstAgentIdx; i < visibleTurns.length; i++) {
    const t = visibleTurns[i]
    if (t.role === 'user') break
    assistantTurns.push(t)
  }

  const turnIds = new Set(assistantTurns.map((t) => t.id))
  const parts = transcript.parts.filter((p) => turnIds.has(p.turn_id))
  const toolRuns = transcript.tool_runs.filter((r) => turnIds.has(r.turn_id))
  return { userTurn, assistantTurns, parts, toolRuns }
}

function Section({
  label,
  action,
  labelButton,
  children,
}: {
  label?: string
  action?: React.ReactNode
  /** Renders in place of the `label` text — for back-affordance headers. */
  labelButton?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        {labelButton ?? (
          <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {label}
          </p>
        )}
        {action}
      </div>
      {children}
    </section>
  )
}

function SessionMeta({ transcript }: { transcript: ConversationTranscript }) {
  const totalIn = transcript.turns.reduce((sum, t) => sum + (t.input_tokens || 0), 0)
  const totalOut = transcript.turns.reduce((sum, t) => sum + (t.output_tokens || 0), 0)
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)

  return (
    <Section label="Session">
      <dl className="space-y-1.5 text-sm">
        <MetaRow term="Provider" detail={transcript.provider ?? '—'} />
        <MetaRow term="Model" detail={transcript.model ?? '—'} />
        <MetaRow term="Updated" detail={relativeTime(transcript.updated_at)} />
        <MetaRow
          term="Turns"
          detail={
            <span className="tabular">
              {visibleTurns.length}
              {transcript.turns.length !== visibleTurns.length ? (
                <span className="text-muted-foreground"> / {transcript.turns.length}</span>
              ) : null}
            </span>
          }
        />
        <MetaRow
          term="Tokens"
          detail={
            <span className="tabular">
              {totalIn.toLocaleString()} in · {totalOut.toLocaleString()} out
            </span>
          }
        />
      </dl>
    </Section>
  )
}

function ExchangeMeta({
  slice,
  transcript,
  onClear,
}: {
  slice: ExchangeSlice
  transcript: ConversationTranscript
  onClear: () => void
}) {
  const totalIn = slice.assistantTurns.reduce((s, t) => s + (t.input_tokens || 0), 0)
  const totalOut = slice.assistantTurns.reduce((s, t) => s + (t.output_tokens || 0), 0)
  const status = slice.assistantTurns.some((t) => t.status === 'streaming' || t.status === 'pending')
    ? 'streaming'
    : slice.assistantTurns.some((t) => t.status === 'interrupted')
      ? 'interrupted'
      : slice.assistantTurns.some((t) => t.error)
        ? 'error'
        : 'complete'
  const lastTurn = slice.assistantTurns[slice.assistantTurns.length - 1]
  // Provider/model captured per-assistant-turn (migration 0006). Older
  // transcripts have null and we fall back to the session's value.
  const firstWithProvider = slice.assistantTurns.find((t) => t.provider) ?? null
  const provider = firstWithProvider?.provider ?? transcript.provider ?? null
  const model = firstWithProvider?.model ?? transcript.model ?? null
  const showFallbackHint =
    !firstWithProvider && slice.assistantTurns.length > 0 && (transcript.provider || transcript.model)

  return (
    <Section
      labelButton={
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 h-7 gap-1.5 px-2 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
          onClick={onClear}
          aria-label="Back to session"
        >
          <ArrowLeft className="size-3.5" />
          Selected exchange
        </Button>
      }
    >
      {slice.userTurn ? (
        <p className="line-clamp-3 rounded-md bg-muted/40 px-3 py-2 text-sm">{slice.userTurn.text}</p>
      ) : null}
      <dl className="space-y-1.5 text-sm">
        <MetaRow term="Status" detail={<span className="capitalize">{status}</span>} />
        <MetaRow term="Iterations" detail={<span className="tabular">{slice.assistantTurns.length}</span>} />
        <MetaRow term="Provider" detail={provider ?? '—'} />
        <MetaRow
          term="Model"
          detail={
            <span>
              {model ?? '—'}
              {showFallbackHint ? (
                <span className="ml-1 text-2xs text-muted-foreground">(session)</span>
              ) : null}
            </span>
          }
        />
        {lastTurn ? <MetaRow term="When" detail={relativeTime(lastTurn.updated_at)} /> : null}
        <MetaRow
          term="Tokens"
          detail={
            <span className="tabular">
              {totalIn.toLocaleString()} in · {totalOut.toLocaleString()} out
            </span>
          }
        />
      </dl>
      {slice.assistantTurns.length > 1 ? (
        <IterationBreakdown turns={slice.assistantTurns} />
      ) : null}
    </Section>
  )
}

function IterationBreakdown({ turns }: { turns: TurnRecord[] }) {
  return (
    <div className="space-y-1.5 pt-1">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Per-iteration
      </p>
      <ul className="space-y-1">
        {turns.map((t, i) => (
          <li
            key={t.id}
            className="flex items-baseline justify-between gap-3 rounded-md bg-muted/30 px-2.5 py-1.5 text-2xs"
          >
            <span className="tabular text-muted-foreground">#{i + 1}</span>
            <span className="truncate font-mono text-2xs text-foreground">
              {t.model ?? '—'}
            </span>
            <span className="tabular shrink-0 text-muted-foreground">
              {(t.input_tokens || 0).toLocaleString()} / {(t.output_tokens || 0).toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function MetaRow({ term, detail }: { term: string; detail: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-foreground">{detail}</dd>
    </div>
  )
}

function ToolRuns({ transcript }: { transcript: ConversationTranscript }) {
  const chat = useChatContext()
  const runs = transcript.tool_runs
  if (runs.length === 0) {
    return (
      <Section label="Tool runs">
        <p className="text-sm text-muted-foreground">No tool calls yet.</p>
      </Section>
    )
  }
  return (
    <Section label={`Tool runs · ${runs.length}`}>
      <ul className="space-y-1.5">
        {runs.map((run) => (
          <ToolRunRow
            key={run.id}
            run={run}
            onSelect={() => handleInspectorToolClick(run, transcript, chat)}
          />
        ))}
      </ul>
    </Section>
  )
}

function ExchangeToolRuns({ slice, transcript }: { slice: ExchangeSlice; transcript: ConversationTranscript }) {
  const chat = useChatContext()
  if (slice.toolRuns.length === 0) {
    return (
      <Section label="Tool runs">
        <p className="text-sm text-muted-foreground">No tool calls in this exchange.</p>
      </Section>
    )
  }
  return (
    <Section label={`Tool runs · ${slice.toolRuns.length}`}>
      <ul className="space-y-1.5">
        {slice.toolRuns.map((run) => (
          <ToolRunRow
            key={run.id}
            run={run}
            onSelect={() => handleInspectorToolClick(run, transcript, chat)}
          />
        ))}
      </ul>
    </Section>
  )
}

function handleInspectorToolClick(
  run: ToolRunRecord,
  transcript: ConversationTranscript,
  chat: ReturnType<typeof useChatContext>,
) {
  const exchangeId = exchangeIdForTurn(transcript, run.turn_id)
  if (run.tool_name === 'execute_sql') {
    // Set both: the inspector switches to ToolRunDetail (toolRunId), and
    // the thread highlights / scrolls to the message (exchangeId).
    chat.selectToolRun(run.id, exchangeId ?? null)
    return
  }
  // Non-SQL tools have no inspectable payload — scope to the surrounding
  // exchange instead so the user sees the message turn this came from.
  if (exchangeId) chat.selectExchange(exchangeId)
}

function exchangeIdForTurn(transcript: ConversationTranscript, turnId: string): string | null {
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)
  const idx = visibleTurns.findIndex((t) => t.id === turnId)
  if (idx < 0) return null
  if (visibleTurns[idx].role === 'user') return visibleTurns[idx].id
  for (let i = idx - 1; i >= 0; i--) {
    if (visibleTurns[i].role === 'user') return visibleTurns[i].id
  }
  return null
}

function ToolRunRow({ run, onSelect }: { run: ToolRunRecord; onSelect?: () => void }) {
  const status = run.status
  const dotClass =
    status === 'completed'
      ? 'bg-success'
      : status === 'error'
        ? 'bg-destructive'
        : 'bg-muted-foreground/60'
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        disabled={!onSelect}
        className={cn(
          'block w-full rounded-md border border-border bg-background px-3 py-2 text-left transition-colors',
          onSelect &&
            'cursor-pointer hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        )}
      >
        <div className="flex items-center gap-2">
          <Database className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="font-mono text-xs font-medium text-foreground">{run.tool_name}</span>
          <span className={cn('ml-auto size-2 rounded-full', dotClass)} aria-hidden />
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-2xs text-muted-foreground">
          <span className="capitalize">{status}</span>
          {typeof run.duration_ms === 'number' ? (
            <>
              <span aria-hidden>·</span>
              <span className="tabular">{run.duration_ms}ms</span>
            </>
          ) : null}
        </div>
      </button>
    </li>
  )
}

function inputDisplayValue(run: ToolRunRecord): string {
  // For execute_sql we show the SQL verbatim — JSON-encoding it just buries
  // the query in escaped quotes and \n.
  if (run.tool_name === 'execute_sql' || run.tool_name === 'run_sql') {
    const sql = (run.input as { sql?: unknown }).sql
    if (typeof sql === 'string') return sql
  }
  return JSON.stringify(run.input, null, 2)
}

function ToolRunDetail({
  run,
  transcript,
  onClear,
  onSelectRun,
}: {
  run: ToolRunRecord
  transcript: ConversationTranscript
  onClear: () => void
  onSelectRun: (id: string) => void
}) {
  // Sibling tool runs in the same exchange — for prev/next nav and the
  // "Tool 2 of 5" breadcrumb. We compute the slice for the run's exchange
  // (not the currently-selected exchange) so the nav is always coherent
  // even if the user click-navigated here from somewhere else.
  const exchangeId = exchangeIdForTurn(transcript, run.turn_id)
  const slice = exchangeId ? sliceForExchange(transcript, exchangeId) : null
  const siblings = slice?.toolRuns ?? [run]
  const idx = siblings.findIndex((r) => r.id === run.id)
  const prev = idx > 0 ? siblings[idx - 1] : null
  const next = idx >= 0 && idx < siblings.length - 1 ? siblings[idx + 1] : null

  // Parent assistant turn carries the provider/model that issued this call.
  const parentTurn = transcript.turns.find((t) => t.id === run.turn_id) ?? null

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 h-7 gap-1.5 px-2 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
          onClick={onClear}
          aria-label="Back to exchange"
        >
          <ArrowLeft className="size-3.5" />
          {siblings.length > 1 && idx >= 0
            ? `Tool ${idx + 1} of ${siblings.length}`
            : 'Tool call'}
        </Button>
        {siblings.length > 1 ? (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => prev && onSelectRun(prev.id)}
              disabled={!prev}
              aria-label="Previous tool call"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => next && onSelectRun(next.id)}
              disabled={!next}
              aria-label="Next tool call"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        ) : null}
      </div>

      <div className="shrink-0 rounded-md border border-border bg-background px-3 py-2">
        <div className="flex items-center gap-2">
          <Database className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="font-mono text-xs font-medium text-foreground">{run.tool_name}</span>
          <span className="ml-auto text-2xs text-muted-foreground capitalize">{run.status}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-muted-foreground">
          {typeof run.duration_ms === 'number' ? (
            <span className="tabular">{run.duration_ms}ms</span>
          ) : null}
          {parentTurn?.model ? (
            <>
              {typeof run.duration_ms === 'number' ? <span aria-hidden>·</span> : null}
              <span className="font-mono">{parentTurn.model}</span>
            </>
          ) : null}
          {parentTurn ? (
            <>
              <span aria-hidden>·</span>
              <span>{relativeTime(run.created_at)}</span>
            </>
          ) : null}
        </div>
        {parentTurn && (parentTurn.input_tokens || parentTurn.output_tokens) ? (
          <p className="mt-1 text-2xs text-muted-foreground tabular">
            Iteration tokens: {(parentTurn.input_tokens || 0).toLocaleString()} in ·{' '}
            {(parentTurn.output_tokens || 0).toLocaleString()} out
          </p>
        ) : null}
      </div>

      <div className="shrink-0">
        <ToolPayload label="Input" value={inputDisplayValue(run)} />
      </div>
      {run.result ? (
        <ToolPayload label="Result" value={prettyJson(run.result)} fillHeight />
      ) : null}
      {run.error ? (
        <div className="shrink-0">
          <ToolPayload label="Error" value={run.error} variant="error" />
        </div>
      ) : null}
      {run.hint ? (
        <div className="shrink-0">
          <ToolPayload label="Hint" value={run.hint} variant="muted" />
        </div>
      ) : null}
    </div>
  )
}

function CompactionHistory({ transcript }: { transcript: ConversationTranscript }) {
  if (transcript.summaries.length === 0) return null
  return (
    <Section label={`Compaction · ${transcript.summaries.length}`}>
      <ul className="space-y-2">
        {transcript.summaries.map((s) => (
          <li
            key={s.id}
            className="rounded-md border border-border bg-background px-3 py-2 text-2xs text-muted-foreground"
          >
            <p className="tabular">{s.source_turn_ids.length} turns compacted</p>
            <p className="tabular text-muted-foreground/80">{relativeTime(s.created_at)}</p>
          </li>
        ))}
      </ul>
    </Section>
  )
}
