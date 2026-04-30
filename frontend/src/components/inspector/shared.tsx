import type { ReactNode } from 'react'
import { Database } from 'lucide-react'

import { exchangeIdForTurn, hasSqlPayload } from '@/lib/transcript'
import type { ConversationTranscript, ToolRunRecord } from '@/lib/types'
import { useChatContext } from '@/lib/chatContext'
import { cn } from '@/lib/utils'

/** Section heading + body. Either `label` (text) or `labelButton`
 *  (custom node — used for the back-affordance in the exchange and
 *  tool-run views). `action` slots into the right side. */
export function Section({
  label,
  action,
  labelButton,
  children,
}: {
  label?: string
  action?: ReactNode
  labelButton?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
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

export function MetaRow({ term, detail }: { term: string; detail: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-foreground">{detail}</dd>
    </div>
  )
}

export function ToolRunRow({
  run,
  onSelect,
}: {
  run: ToolRunRecord
  onSelect?: () => void
}) {
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

/** Hook returning the click handler for a tool-run row. SQL-bearing
 *  tools open the ToolRunDetail viewer (and also highlight the
 *  surrounding exchange in the thread). Non-SQL tools just scope the
 *  inspector to the surrounding exchange — they have no inspectable
 *  payload of their own. */
export function useInspectorToolClick(transcript: ConversationTranscript) {
  const chat = useChatContext()
  return (run: ToolRunRecord) => {
    const exchangeId = exchangeIdForTurn(transcript, run.turn_id)
    if (hasSqlPayload(run.tool_name)) {
      chat.selectToolRun(run.id, exchangeId ?? null)
      return
    }
    if (exchangeId) chat.selectExchange(exchangeId)
  }
}
