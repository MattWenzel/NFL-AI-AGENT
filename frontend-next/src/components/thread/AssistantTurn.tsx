import { AlertTriangle, Loader2 } from 'lucide-react'

import { Markdown } from '@/components/thread/Markdown'
import { ToolCallCard } from '@/components/thread/ToolCallCard'
import type {
  AssistantPartRecord,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

interface AssistantTurnProps {
  turn: TurnRecord
  parts: AssistantPartRecord[]
  toolRuns: ToolRunRecord[]
}

export function AssistantTurn({ turn, parts, toolRuns }: AssistantTurnProps) {
  const orderedParts = [...parts].sort((a, b) => a.order_index - b.order_index)
  const toolRunById = new Map(toolRuns.map((r) => [r.id, r]))

  const isStreaming = turn.status === 'pending' || turn.status === 'streaming'
  const hasContent = orderedParts.length > 0

  return (
    <div className="space-y-2">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Agent
      </p>
      {orderedParts.map((part) => {
        if (part.kind === 'text') {
          return part.content.trim() ? (
            <Markdown key={part.id} source={part.content} />
          ) : null
        }
        if (part.kind === 'tool_use') {
          const run = part.tool_run_id ? toolRunById.get(part.tool_run_id) : undefined
          if (!run) {
            return (
              <p
                key={part.id}
                className="font-mono text-xs text-muted-foreground"
              >
                tool: {part.name ?? 'unknown'}
              </p>
            )
          }
          return <ToolCallCard key={part.id} run={run} />
        }
        if (part.kind === 'thinking') {
          // Thinking blocks render as muted, smaller text — visible but de-emphasized.
          return part.content.trim() ? (
            <details key={part.id} className="text-sm text-muted-foreground">
              <summary className="cursor-pointer text-2xs font-medium uppercase tracking-[0.14em] hover:text-foreground">
                Thinking
              </summary>
              <p className="mt-1 whitespace-pre-wrap leading-relaxed">{part.content}</p>
            </details>
          ) : null
        }
        return null
      })}

      {isStreaming && !hasContent ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          <span>Thinking…</span>
        </p>
      ) : null}

      {turn.error ? (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="size-4 shrink-0 mt-px" />
          <span>{turn.error}</span>
        </div>
      ) : null}

      {turn.status === 'complete' && (turn.input_tokens > 0 || turn.output_tokens > 0) ? (
        <p className="pt-1 text-2xs text-muted-foreground tabular">
          {turn.input_tokens.toLocaleString()} in · {turn.output_tokens.toLocaleString()} out
        </p>
      ) : null}
    </div>
  )
}
