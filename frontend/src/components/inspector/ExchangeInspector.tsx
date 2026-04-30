import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useChatContext } from '@/lib/chatContext'
import { relativeTime } from '@/lib/datetime'
import { sliceForExchange, type ExchangeSlice } from '@/lib/transcript'
import type { ConversationTranscript, TurnRecord } from '@/lib/types'

import { MetaRow, Section, ToolRunRow, useInspectorToolClick } from './shared'

/** Inspector view for a single user/assistant exchange — meta for the
 *  user turn that triggered it, an iteration breakdown when the agent
 *  ran multi-step, and the tool runs scoped to those iterations. */
export function ExchangeInspector({
  transcript,
  exchangeId,
}: {
  transcript: ConversationTranscript
  exchangeId: string
}) {
  const slice = sliceForExchange(transcript, exchangeId)
  if (!slice) return null

  return (
    <div className="flex flex-1 flex-col gap-7 overflow-y-auto p-5">
      <ExchangeMeta slice={slice} transcript={transcript} />
      <ExchangeToolRuns slice={slice} transcript={transcript} />
    </div>
  )
}

function ExchangeMeta({
  slice,
  transcript,
}: {
  slice: ExchangeSlice
  transcript: ConversationTranscript
}) {
  const chat = useChatContext()
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
          onClick={() => chat.selectExchange(null)}
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
      <dl className="flex flex-col gap-1.5 text-sm">
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
    <div className="flex flex-col gap-1.5 pt-1">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Per-iteration
      </p>
      <ul className="flex flex-col gap-1">
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

function ExchangeToolRuns({
  slice,
  transcript,
}: {
  slice: ExchangeSlice
  transcript: ConversationTranscript
}) {
  const handleClick = useInspectorToolClick(transcript)
  if (slice.toolRuns.length === 0) {
    return (
      <Section label="Tool runs">
        <p className="text-sm text-muted-foreground">No tool calls in this exchange.</p>
      </Section>
    )
  }
  return (
    <Section label={`Tool runs · ${slice.toolRuns.length}`}>
      <ul className="flex flex-col gap-1.5">
        {slice.toolRuns.map((run) => (
          <ToolRunRow key={run.id} run={run} onSelect={() => handleClick(run)} />
        ))}
      </ul>
    </Section>
  )
}
