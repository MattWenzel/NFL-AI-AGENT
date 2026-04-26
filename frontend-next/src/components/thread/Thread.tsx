import { useMemo } from 'react'

import { AssistantTurn } from '@/components/thread/AssistantTurn'
import { UserTurn } from '@/components/thread/UserTurn'
import type { ConversationTranscript } from '@/lib/types'

interface ThreadProps {
  transcript: ConversationTranscript
}

export function Thread({ transcript }: ThreadProps) {
  const groups = useMemo(() => {
    const partsByTurn = new Map<string, typeof transcript.parts>()
    const runsByTurn = new Map<string, typeof transcript.tool_runs>()
    for (const part of transcript.parts) {
      const list = partsByTurn.get(part.turn_id) ?? []
      list.push(part)
      partsByTurn.set(part.turn_id, list)
    }
    for (const run of transcript.tool_runs) {
      const list = runsByTurn.get(run.turn_id) ?? []
      list.push(run)
      runsByTurn.set(run.turn_id, list)
    }
    return { partsByTurn, runsByTurn }
  }, [transcript])

  const visibleTurns = transcript.turns.filter((t) => !t.compacted)

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-10 space-y-10 lg:px-10">
        {transcript.title ? (
          <header className="space-y-1 pb-2">
            <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Conversation
            </p>
            <h1 className="font-display text-3xl font-medium tracking-tight text-foreground">
              {transcript.title}
            </h1>
          </header>
        ) : null}
        {visibleTurns.map((turn) =>
          turn.role === 'user' ? (
            <UserTurn key={turn.id} turn={turn} />
          ) : (
            <AssistantTurn
              key={turn.id}
              turn={turn}
              parts={groups.partsByTurn.get(turn.id) ?? []}
              toolRuns={groups.runsByTurn.get(turn.id) ?? []}
            />
          ),
        )}
      </div>
    </div>
  )
}
