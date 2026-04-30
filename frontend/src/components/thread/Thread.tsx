import { useEffect, useMemo, useRef } from 'react'

import { AgentResponse } from '@/components/thread/AgentResponse'
import { UserTurn } from '@/components/thread/UserTurn'
import { useLayout } from '@/components/layout/AppShell'
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
  const { selectedExchangeId, selectedToolRunId, selectExchange, clearSelection } = useChatContext()
  const { openDesktopInspector, closeDesktopInspector } = useLayout()
  const pickExchange = (id: string) => {
    openDesktopInspector()
    selectExchange(id)
  }
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

  // First-rendered group per exchange anchors the scroll target — usually the
  // user turn, falling back to the agent group when no user turn precedes it.
  const anchorFlags = useMemo(() => {
    const seen = new Set<string>()
    return groups.map((g) => {
      if (seen.has(g.exchangeId)) return false
      seen.add(g.exchangeId)
      return true
    })
  }, [groups])

  const exchangeRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  useEffect(() => {
    if (!selectedExchangeId) return
    const el = exchangeRefs.current.get(selectedExchangeId)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [selectedExchangeId])

  // Scroll the most recent user turn into view: smoothly on send (so the
  // user follows their newly-posted message), instantly on conversation
  // switch or first load (avoids a jarring smooth-scroll across history).
  // Keyed on the user-turn id so this fires once per send, not on every
  // text-delta during streaming.
  const lastUserTurnId = useMemo(() => {
    for (let i = groups.length - 1; i >= 0; i--) {
      if (groups[i].kind === 'user') return groups[i].exchangeId
    }
    return null
  }, [groups])
  const prevSessionIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!lastUserTurnId) return
    const el = exchangeRefs.current.get(lastUserTurnId)
    if (!el) return
    const isNewConversation = prevSessionIdRef.current !== transcript.session_id
    prevSessionIdRef.current = transcript.session_id
    el.scrollIntoView({
      behavior: isNewConversation ? 'auto' : 'smooth',
      block: 'start',
    })
  }, [lastUserTurnId, transcript.session_id])

  return (
    <div
      className="flex-1 overflow-y-auto"
      onClick={() => {
        // Clicks that reach this far didn't hit a message — message and tool
        // clicks stopPropagation, so this only fires for empty thread space.
        if (selectedExchangeId || selectedToolRunId) clearSelection()
        closeDesktopInspector()
      }}
    >
      <div className="mx-auto w-full max-w-6xl space-y-8 px-6 py-6 lg:px-10">
        {groups.map((g, i) => {
          const setAnchorRef = anchorFlags[i]
            ? (el: HTMLDivElement | null) => {
                const map = exchangeRefs.current
                if (el) map.set(g.exchangeId, el)
                else map.delete(g.exchangeId)
              }
            : undefined
          return (
            <div
              key={g.kind === 'user' ? g.turn.id : g.turns[0]?.id ?? `agent-${i}`}
              ref={setAnchorRef}
            >
              {g.kind === 'user' ? (
                <UserTurn
                  turn={g.turn}
                  selected={selectedExchangeId === g.exchangeId}
                  onSelect={() => pickExchange(g.exchangeId)}
                />
              ) : (
                <AgentResponse
                  turns={g.turns}
                  parts={g.parts}
                  toolRuns={g.toolRuns}
                  exchangeId={g.exchangeId}
                  selected={selectedExchangeId === g.exchangeId}
                  onSelect={() => pickExchange(g.exchangeId)}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
