import { Database, X } from 'lucide-react'

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
      <div className="space-y-6 p-5">
        <Section label="Session">
          <p className="text-sm text-muted-foreground">
            No conversation selected. Open a chat or start a new one to inspect runtime details.
          </p>
        </Section>
      </div>
    )
  }

  if (chat.selectedExchangeId) {
    const slice = sliceForExchange(transcript, chat.selectedExchangeId)
    if (slice) {
      return (
        <div className="space-y-7 p-5">
          <ExchangeMeta
            slice={slice}
            transcript={transcript}
            onClear={() => chat.selectExchange(null)}
          />
          <ExchangeToolRuns slice={slice} />
        </div>
      )
    }
  }

  return (
    <div className="space-y-7 p-5">
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

function Section({ label, action, children }: { label: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </p>
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
    : slice.assistantTurns.some((t) => t.error)
      ? 'error'
      : 'complete'
  const lastTurn = slice.assistantTurns[slice.assistantTurns.length - 1]

  return (
    <Section
      label="Selected exchange"
      action={
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={onClear}
          aria-label="Clear selection"
        >
          <X className="size-3.5" />
        </Button>
      }
    >
      {slice.userTurn ? (
        <p className="line-clamp-3 rounded-md bg-muted/40 px-3 py-2 text-sm">{slice.userTurn.text}</p>
      ) : null}
      <dl className="space-y-1.5 text-sm">
        <MetaRow term="Status" detail={<span className="capitalize">{status}</span>} />
        <MetaRow term="Iterations" detail={<span className="tabular">{slice.assistantTurns.length}</span>} />
        <MetaRow term="Provider" detail={transcript.provider ?? '—'} />
        <MetaRow term="Model" detail={transcript.model ?? '—'} />
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
    </Section>
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
          <ToolRunRow key={run.id} run={run} />
        ))}
      </ul>
    </Section>
  )
}

function ExchangeToolRuns({ slice }: { slice: ExchangeSlice }) {
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
          <ToolRunRow key={run.id} run={run} />
        ))}
      </ul>
    </Section>
  )
}

function ToolRunRow({ run }: { run: ToolRunRecord }) {
  const status = run.status
  const dotClass =
    status === 'completed'
      ? 'bg-accent'
      : status === 'error'
        ? 'bg-destructive'
        : 'bg-muted-foreground/60'
  // Mirror the in-thread thinking block: only execute_sql carries payloads
  // worth surfacing — guides and schema lookups have no useful detail.
  const showDetails =
    run.tool_name === 'execute_sql' && (!!run.result || !!run.error || !!run.hint)
  return (
    <li className="rounded-md border border-border bg-background px-3 py-2">
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
      {showDetails ? (
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
