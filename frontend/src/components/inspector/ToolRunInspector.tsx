import { ArrowLeft, ChevronLeft, ChevronRight, Database } from 'lucide-react'

import { ToolPayload, prettyJson } from '@/components/thread/ToolPayload'
import { Button } from '@/components/ui/button'
import { useChatContext } from '@/lib/state/chatContext'
import { relativeTime } from '@/lib/datetime'
import {
  exchangeIdForTurn,
  inputDisplayValue,
  sliceForExchange,
} from '@/lib/transcript'
import type { ConversationTranscript, ToolRunRecord } from '@/lib/types'

/** Detail view for a single tool run: prev/next nav across siblings in
 *  the same exchange, the tool's input (rendered as raw SQL when
 *  applicable), the result, and any error/hint payload. */
export function ToolRunInspector({
  run,
  transcript,
}: {
  run: ToolRunRecord
  transcript: ConversationTranscript
}) {
  const chat = useChatContext()
  // Sibling tool runs in the same exchange — for prev/next nav and the
  // "Tool 2 of 5" breadcrumb. We slice based on the run's exchange (not
  // the currently-selected one) so nav stays coherent even if the user
  // click-navigated here from somewhere else.
  const exchangeId = exchangeIdForTurn(transcript, run.turn_id)
  const slice = exchangeId ? sliceForExchange(transcript, exchangeId) : null
  const siblings = slice?.toolRuns ?? [run]
  const idx = siblings.findIndex((r) => r.id === run.id)
  const prev = idx > 0 ? siblings[idx - 1] : null
  const next = idx >= 0 && idx < siblings.length - 1 ? siblings[idx + 1] : null

  // Parent assistant turn carries the provider/model that issued this call.
  const parentTurn = transcript.turns.find((t) => t.id === run.turn_id) ?? null

  const selectRun = (id: string) =>
    chat.selectToolRun(id, exchangeIdForTurn(transcript, run.turn_id))

  return (
    <div className="flex min-h-0 flex-1 flex-col p-5">
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex shrink-0 items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 gap-1.5 px-2 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
            onClick={() => chat.selectToolRun(null)}
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
                onClick={() => prev && selectRun(prev.id)}
                disabled={!prev}
                aria-label="Previous tool call"
              >
                <ChevronLeft className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => next && selectRun(next.id)}
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
    </div>
  )
}
