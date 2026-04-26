import { useMemo } from 'react'

import { AgentResponse } from '@/components/thread/AgentResponse'
import { UserTurn } from '@/components/thread/UserTurn'
import { useChatContext } from '@/lib/chatContext'
import type {
  AssistantPartRecord,
  ConversationTranscript,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

interface ThreadProps {
  transcript: ConversationTranscript
}

type ThreadGroup =
  | { kind: 'user'; turn: TurnRecord; exchangeId: string }
  | {
      kind: 'agent'
      turns: TurnRecord[]
      parts: AssistantPartRecord[]
      toolRuns: ToolRunRecord[]
      exchangeId: string
    }

export function Thread({ transcript }: ThreadProps) {
  const { selectedExchangeId, selectExchange } = useChatContext()
  const groups = useMemo<ThreadGroup[]>(() => {
    const partsByTurn = new Map<string, AssistantPartRecord[]>()
    const runsByTurn = new Map<string, ToolRunRecord[]>()
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

    const visibleTurns = transcript.turns.filter((t) => !t.compacted)
    const out: ThreadGroup[] = []
    let agent: Extract<ThreadGroup, { kind: 'agent' }> | null = null
    // The most recent user turn anchors the exchange; an agent group inherits
    // its preceding user turn's id so clicking either side of a Q/A pair
    // selects the same slice in the inspector.
    let currentExchangeId: string | null = null

    for (const turn of visibleTurns) {
      if (turn.role === 'user') {
        if (agent) {
          out.push(agent)
          agent = null
        }
        currentExchangeId = turn.id
        out.push({ kind: 'user', turn, exchangeId: turn.id })
      } else {
        const anchor = currentExchangeId ?? turn.id
        if (!agent) {
          agent = { kind: 'agent', turns: [], parts: [], toolRuns: [], exchangeId: anchor }
        }
        agent.turns.push(turn)
        const parts = (partsByTurn.get(turn.id) ?? [])
          .slice()
          .sort((a, b) => a.order_index - b.order_index)
        agent.parts.push(...parts)
        agent.toolRuns.push(...(runsByTurn.get(turn.id) ?? []))
      }
    }
    if (agent) out.push(agent)
    return out
  }, [transcript])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl space-y-8 px-6 py-6 lg:px-10">
        {groups.map((g, i) =>
          g.kind === 'user' ? (
            <UserTurn
              key={g.turn.id}
              turn={g.turn}
              selected={selectedExchangeId === g.exchangeId}
              onSelect={() => selectExchange(g.exchangeId)}
            />
          ) : (
            <AgentResponse
              key={g.turns[0]?.id ?? `agent-${i}`}
              turns={g.turns}
              parts={g.parts}
              toolRuns={g.toolRuns}
              selected={selectedExchangeId === g.exchangeId}
              onSelect={() => selectExchange(g.exchangeId)}
            />
          ),
        )}
      </div>
    </div>
  )
}
