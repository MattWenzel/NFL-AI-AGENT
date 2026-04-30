import { relativeTime } from '@/lib/datetime'
import type { ConversationTranscript } from '@/lib/types'

import { MetaRow, Section, ToolRunRow, useInspectorToolClick } from './shared'

/** The default inspector view — session-level metadata, the full
 *  tool-run list, and any compaction history. Shown when nothing is
 *  selected (no exchange, no specific tool run). */
export function SessionInspector({ transcript }: { transcript: ConversationTranscript }) {
  return (
    <div className="flex flex-1 flex-col gap-7 overflow-y-auto p-5">
      <SessionMeta transcript={transcript} />
      <ToolRuns transcript={transcript} />
      <CompactionHistory transcript={transcript} />
    </div>
  )
}

function SessionMeta({ transcript }: { transcript: ConversationTranscript }) {
  const totalIn = transcript.turns.reduce((sum, t) => sum + (t.input_tokens || 0), 0)
  const totalOut = transcript.turns.reduce((sum, t) => sum + (t.output_tokens || 0), 0)
  const visibleTurns = transcript.turns.filter((t) => !t.compacted)

  return (
    <Section label="Session">
      <dl className="flex flex-col gap-1.5 text-sm">
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

function ToolRuns({ transcript }: { transcript: ConversationTranscript }) {
  const handleClick = useInspectorToolClick(transcript)
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
      <ul className="flex flex-col gap-1.5">
        {runs.map((run) => (
          <ToolRunRow key={run.id} run={run} onSelect={() => handleClick(run)} />
        ))}
      </ul>
    </Section>
  )
}

function CompactionHistory({ transcript }: { transcript: ConversationTranscript }) {
  if (transcript.summaries.length === 0) return null
  return (
    <Section label={`Compaction · ${transcript.summaries.length}`}>
      <ul className="flex flex-col gap-2">
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
