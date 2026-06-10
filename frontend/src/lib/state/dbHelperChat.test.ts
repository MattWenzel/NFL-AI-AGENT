import { describe, expect, it } from 'vitest'

import { isHelperMessage, isHelperToolRun } from './dbHelperChat'

describe('sessionStorage hydration validators', () => {
  it('accepts well-formed messages', () => {
    expect(isHelperMessage({ role: 'user', text: 'hi' })).toBe(true)
    expect(
      isHelperMessage({
        role: 'assistant',
        text: '',
        toolRuns: [
          {
            id: 't1',
            name: 'execute_sql',
            input: { sql: 'SELECT 1' },
            content: '{"rows":[]}',
            status: 'completed',
            error: null,
          },
        ],
      }),
    ).toBe(true)
  })

  it('rejects malformed messages instead of letting them reach the renderer', () => {
    expect(isHelperMessage(null)).toBe(false)
    expect(isHelperMessage('hi')).toBe(false)
    expect(isHelperMessage({ role: 'system', text: 'x' })).toBe(false)
    expect(isHelperMessage({ role: 'user' })).toBe(false)
    expect(isHelperMessage({ role: 'user', text: 42 })).toBe(false)
    expect(isHelperMessage({ role: 'assistant', text: '', toolRuns: 'oops' })).toBe(false)
    expect(isHelperMessage({ role: 'assistant', text: '', toolRuns: [{ id: 1 }] })).toBe(false)
  })

  it('validates tool-run shape strictly', () => {
    const base = {
      id: 't1',
      name: 'execute_sql',
      input: {},
      content: null,
      status: 'pending',
      error: null,
    }
    expect(isHelperToolRun(base)).toBe(true)
    expect(isHelperToolRun({ ...base, status: 'weird' })).toBe(false)
    expect(isHelperToolRun({ ...base, content: 42 })).toBe(false)
    expect(isHelperToolRun({ ...base, input: null })).toBe(false)
  })
})
