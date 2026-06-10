import { describe, expect, it } from 'vitest'

import { reduce, type Action, type ChatState } from './chatStore'
import type { TurnRecord } from '@/lib/types'

const INITIAL: ChatState = {
  conversationId: null,
  transcript: null,
  conversations: [],
  conversationsStatus: 'idle',
  streamStatus: 'idle',
  streamError: null,
}

function userTurn(id = 'u1'): TurnRecord {
  const now = '2026-01-01T00:00:00Z'
  return {
    id,
    role: 'user',
    status: 'complete',
    text: 'hello',
    compacted: false,
    error: null,
    input_tokens: 0,
    output_tokens: 0,
    provider: null,
    model: null,
    created_at: now,
    updated_at: now,
  }
}

/** Replay a streaming exchange the way `send()` dispatches it. */
function play(actions: Action[], from: ChatState = INITIAL): ChatState {
  return actions.reduce(reduce, from)
}

describe('chatStore reducer — streaming exchange', () => {
  const exchange: Action[] = [
    { type: 'optimistic-user-turn', turn: userTurn() },
    { type: 'set-conversation-id', id: 'c1' },
    { type: 'assistant-started', turnId: 'a1' },
    { type: 'text-delta', text: 'The answer ' },
    { type: 'text-delta', text: 'is 42.' },
  ]

  it('builds a transcript from an optimistic send', () => {
    const state = play(exchange)
    expect(state.conversationId).toBe('c1')
    expect(state.streamStatus).toBe('streaming')
    expect(state.transcript?.turns.map((t) => t.id)).toEqual(['u1', 'a1'])
  })

  it('coalesces consecutive text deltas into one part', () => {
    const state = play(exchange)
    const textParts = state.transcript!.parts.filter((p) => p.kind === 'text')
    expect(textParts).toHaveLength(1)
    expect(textParts[0].content).toBe('The answer is 42.')
  })

  it('tool calls break the text run and register a running tool', () => {
    const state = play([
      ...exchange,
      { type: 'tool-call', toolRunId: 't1', name: 'execute_sql', input: { sql: 'SELECT 1' } },
      { type: 'text-delta', text: 'After the tool.' },
    ])
    const parts = state.transcript!.parts
    expect(parts.map((p) => p.kind)).toEqual(['text', 'tool_use', 'text'])
    expect(state.transcript!.tool_runs).toHaveLength(1)
    expect(state.transcript!.tool_runs[0].status).toBe('running')
  })

  it('tool results settle the matching run only', () => {
    const state = play([
      ...exchange,
      { type: 'tool-call', toolRunId: 't1', name: 'execute_sql', input: {} },
      { type: 'tool-call', toolRunId: 't2', name: 'get_schema', input: {} },
      { type: 'tool-result', toolRunId: 't2', status: 'error', message: 'boom' },
    ])
    const byId = Object.fromEntries(state.transcript!.tool_runs.map((r) => [r.id, r]))
    expect(byId.t1.status).toBe('running')
    expect(byId.t2.status).toBe('error')
    expect(byId.t2.error).toBe('boom')
  })

  it('duplicate assistant-started is idempotent', () => {
    const state = play([...exchange, { type: 'assistant-started', turnId: 'a1' }])
    expect(state.transcript!.turns.filter((t) => t.id === 'a1')).toHaveLength(1)
  })

  it('stream-error preserves the transcript and surfaces the message', () => {
    const state = play([...exchange, { type: 'stream-error', message: 'rate limited' }])
    expect(state.streamStatus).toBe('error')
    expect(state.streamError).toBe('rate limited')
    expect(state.transcript?.turns).toHaveLength(2)
  })

  it('stream-done returns to idle', () => {
    const state = play([...exchange, { type: 'stream-done' }])
    expect(state.streamStatus).toBe('idle')
  })

  it('new-conversation drops the transcript but keeps the sidebar list', () => {
    const mid = play(exchange)
    const state = reduce(
      { ...mid, conversations: [{ id: 'c1' } as ChatState['conversations'][number]] },
      { type: 'new-conversation' },
    )
    expect(state.transcript).toBeNull()
    expect(state.conversationId).toBeNull()
    expect(state.conversations).toHaveLength(1)
  })

  it('text-delta without a transcript is a no-op', () => {
    expect(reduce(INITIAL, { type: 'text-delta', text: 'x' })).toBe(INITIAL)
  })
})

describe('chatStore reducer — set-transcript merging', () => {
  it('merges consecutive same-turn text parts in order_index order', () => {
    const now = '2026-01-01T00:00:00Z'
    const state = reduce(INITIAL, {
      type: 'set-transcript',
      transcript: {
        session_id: 'c1',
        title: null,
        provider: null,
        model: null,
        updated_at: null,
        turns: [],
        parts: [
          { id: 'p2', turn_id: 'a1', kind: 'text', order_index: 1, content: ' world', created_at: now },
          { id: 'p1', turn_id: 'a1', kind: 'text', order_index: 0, content: 'hello', created_at: now },
          { id: 'p3', turn_id: 'a1', kind: 'tool_use', order_index: 2, content: '', created_at: now },
          { id: 'p4', turn_id: 'a1', kind: 'text', order_index: 3, content: 'after', created_at: now },
        ],
        tool_runs: [],
        summaries: [],
      },
    })
    const parts = state.transcript!.parts
    expect(parts.map((p) => [p.kind, p.content])).toEqual([
      ['text', 'hello world'],
      ['tool_use', ''],
      ['text', 'after'],
    ])
  })
})
