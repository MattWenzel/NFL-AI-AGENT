import { useMemo } from 'react'

import { AgentResponse } from '@/components/thread/AgentResponse'
import { UserTurn } from '@/components/thread/UserTurn'
import { useLayout } from '@/components/layout/AppShell'
import { cn } from '@/lib/utils'
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
  | { kind: 'user'; turn: TurnRecord }
  | { kind: 'agent'; turns: TurnRecord[]; parts: AssistantPartRecord[]; toolRuns: ToolRunRecord[] }

export function Thread({ transcript }: ThreadProps) {
  const { desktopSidebarOpen } = useLayout()
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

    for (const turn of visibleTurns) {
      if (turn.role === 'user') {
        if (agent) {
          out.push(agent)
          agent = null
        }
        out.push({ kind: 'user', turn })
      } else {
        if (!agent) agent = { kind: 'agent', turns: [], parts: [], toolRuns: [] }
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
      <div
        className={cn(
          'max-w-5xl space-y-8 px-6 py-6 lg:px-10',
          !desktopSidebarOpen && 'mx-auto',
        )}
      >
        {groups.map((g, i) =>
          g.kind === 'user' ? (
            <UserTurn key={g.turn.id} turn={g.turn} />
          ) : (
            <AgentResponse
              key={g.turns[0]?.id ?? `agent-${i}`}
              turns={g.turns}
              parts={g.parts}
              toolRuns={g.toolRuns}
            />
          ),
        )}
      </div>
    </div>
  )
}
