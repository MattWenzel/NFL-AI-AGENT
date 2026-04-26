import { Database } from 'lucide-react'

import { useChatContext } from '@/lib/chatContext'
import { relativeTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { ConversationTranscript, ToolRunRecord } from '@/lib/types'

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

  return (
    <div className="space-y-7 p-5">
      <SessionMeta transcript={transcript} />
      <ToolRuns transcript={transcript} />
      <CompactionHistory transcript={transcript} />
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
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

function ToolRunRow({ run }: { run: ToolRunRecord }) {
  const status = run.status
  const dotClass =
    status === 'completed'
      ? 'bg-accent'
      : status === 'error'
        ? 'bg-destructive'
        : 'bg-muted-foreground/60'
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
