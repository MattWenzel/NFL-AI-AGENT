import { useChatContext } from '@/lib/chatContext'

import { ExchangeInspector } from './ExchangeInspector'
import { Section } from './shared'
import { SessionInspector } from './SessionInspector'
import { ToolRunInspector } from './ToolRunInspector'

/** Top-level inspector — picks one of three views based on the chat
 *  store's selection state. The actual content lives in the focused
 *  subcomponents so each view can evolve (or be replaced per-surface)
 *  without disturbing the others. */
export function Inspector() {
  const chat = useChatContext()
  const transcript = chat.transcript

  if (!transcript || transcript.turns.length === 0) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-5">
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
    if (run) return <ToolRunInspector run={run} transcript={transcript} />
  }

  if (chat.selectedExchangeId) {
    return (
      <ExchangeInspector
        transcript={transcript}
        exchangeId={chat.selectedExchangeId}
      />
    )
  }

  return <SessionInspector transcript={transcript} />
}
