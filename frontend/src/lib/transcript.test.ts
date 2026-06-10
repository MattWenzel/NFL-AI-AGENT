import { describe, expect, it } from 'vitest'

import {
  exchangeIdForTurn,
  hasSqlPayload,
  inputDisplayValue,
  sliceForExchange,
} from './transcript'
import type { ConversationTranscript, ToolRunRecord, TurnRecord } from '@/lib/types'

const NOW = '2026-01-01T00:00:00Z'

function turn(id: string, role: 'user' | 'assistant', compacted = false): TurnRecord {
  return {
    id,
    role,
    status: 'complete',
    text: role === 'user' ? `msg-${id}` : '',
    compacted,
    error: null,
    input_tokens: 0,
    output_tokens: 0,
    provider: null,
    model: null,
    created_at: NOW,
    updated_at: NOW,
  }
}

function toolRun(id: string, turnId: string, input: Record<string, unknown>): ToolRunRecord {
  return {
    id,
    turn_id: turnId,
    tool_name: 'execute_sql',
    input,
    status: 'completed',
    result: null,
    error: null,
    duration_ms: 1,
    compacted: false,
    created_at: NOW,
    updated_at: NOW,
  }
}

function transcript(): ConversationTranscript {
  return {
    session_id: 'c1',
    title: null,
    provider: null,
    model: null,
    updated_at: null,
    // u1 → a1, a2 (multi-iteration), then u2 → a3
    turns: [turn('u1', 'user'), turn('a1', 'assistant'), turn('a2', 'assistant'), turn('u2', 'user'), turn('a3', 'assistant')],
    parts: [],
    tool_runs: [toolRun('t1', 'a1', { sql: 'SELECT 1' }), toolRun('t2', 'a3', {})],
    summaries: [],
  }
}

describe('sliceForExchange', () => {
  it('anchored on a user turn, collects all assistant turns until the next user turn', () => {
    const slice = sliceForExchange(transcript(), 'u1')!
    expect(slice.userTurn?.id).toBe('u1')
    expect(slice.assistantTurns.map((t) => t.id)).toEqual(['a1', 'a2'])
    expect(slice.toolRuns.map((r) => r.id)).toEqual(['t1'])
  })

  it('anchored on an assistant turn, walks back to the triggering user turn', () => {
    const slice = sliceForExchange(transcript(), 'a2')!
    expect(slice.userTurn?.id).toBe('u1')
    expect(slice.assistantTurns.map((t) => t.id)).toEqual(['a1', 'a2'])
  })

  it('returns null for unknown ids', () => {
    expect(sliceForExchange(transcript(), 'nope')).toBeNull()
  })

  it('skips compacted turns', () => {
    const t = transcript()
    t.turns[0] = turn('u1', 'user', true)
    expect(sliceForExchange(t, 'u1')).toBeNull()
  })
})

describe('exchangeIdForTurn', () => {
  it('resolves an assistant turn to its user anchor', () => {
    expect(exchangeIdForTurn(transcript(), 'a3')).toBe('u2')
  })
  it('resolves a user turn to itself', () => {
    expect(exchangeIdForTurn(transcript(), 'u2')).toBe('u2')
  })
})

describe('inputDisplayValue', () => {
  it('renders SQL tools as the raw query', () => {
    expect(inputDisplayValue(toolRun('t', 'a', { sql: 'SELECT 1' }))).toBe('SELECT 1')
  })
  it('renders non-SQL inputs as pretty JSON', () => {
    const run = { ...toolRun('t', 'a', { name: 'Mahomes' }), tool_name: 'search_players' }
    expect(inputDisplayValue(run)).toBe(JSON.stringify({ name: 'Mahomes' }, null, 2))
  })
})

describe('hasSqlPayload', () => {
  it('matches the SQL-bearing tools', () => {
    expect(hasSqlPayload('execute_sql')).toBe(true)
    expect(hasSqlPayload('set_table')).toBe(true)
    expect(hasSqlPayload('search_players')).toBe(false)
  })
})
