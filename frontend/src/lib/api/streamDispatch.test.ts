import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamAndDispatch, type StreamHandlers } from './streamDispatch'
import type { SseEvent } from './sse'

// Drive the dispatcher with canned events instead of a live stream.
vi.mock('@/lib/api/sse', () => ({
  openSseStream: vi.fn(),
}))

import { openSseStream } from '@/lib/api/sse'

function feed(events: SseEvent[]) {
  vi.mocked(openSseStream).mockImplementationOnce(async function* () {
    for (const event of events) yield event
  })
}

async function dispatch(events: SseEvent[], handlers: StreamHandlers) {
  feed(events)
  await streamAndDispatch('/chat/stream', {}, handlers)
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('streamAndDispatch', () => {
  it('routes each event type to its handler', async () => {
    const onText = vi.fn()
    const onToolCall = vi.fn()
    const onToolResult = vi.fn()
    const onConversationId = vi.fn()
    const onDone = vi.fn()

    await dispatch(
      [
        { type: 'conversation_id', id: 'c1' },
        { type: 'text', text: 'hi' },
        { type: 'tool_call', tool_run_id: 't1', name: 'execute_sql', input: { sql: 'SELECT 1' } },
        { type: 'tool_result', tool_run_id: 't1', name: 'execute_sql', content: '{"rows":[]}' },
        { type: 'done' },
      ],
      { onText, onToolCall, onToolResult, onConversationId, onDone },
    )

    expect(onConversationId).toHaveBeenCalledWith('c1')
    expect(onText).toHaveBeenCalledWith('hi')
    expect(onToolCall).toHaveBeenCalledWith({
      tool_run_id: 't1',
      name: 'execute_sql',
      input: { sql: 'SELECT 1' },
    })
    expect(onToolResult).toHaveBeenCalledWith({
      tool_run_id: 't1',
      name: 'execute_sql',
      content: '{"rows":[]}',
    })
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('guards malformed fields instead of forwarding garbage', async () => {
    const onText = vi.fn()
    const onToolCall = vi.fn()
    await dispatch(
      [
        { type: 'text', text: 42 } as unknown as SseEvent,
        { type: 'tool_call', tool_run_id: 7, name: 'x' } as unknown as SseEvent,
      ],
      { onText, onToolCall },
    )
    expect(onText).not.toHaveBeenCalled()
    expect(onToolCall).not.toHaveBeenCalled()
  })

  it('silently drops unknown event types', async () => {
    const onError = vi.fn()
    await dispatch([{ type: 'mystery_event' }], { onError })
    expect(onError).not.toHaveBeenCalled()
  })

  it('defaults error message when the payload lacks one', async () => {
    const onError = vi.fn()
    await dispatch([{ type: 'error' }], { onError })
    expect(onError).toHaveBeenCalledWith('Stream error')
  })

  it('fills tool_failed defaults', async () => {
    const onToolFailed = vi.fn()
    await dispatch(
      [{ type: 'tool_failed', tool_run_id: 't1' }],
      { onToolFailed },
    )
    expect(onToolFailed).toHaveBeenCalledWith({
      tool_run_id: 't1',
      name: '',
      content: '',
      message: 'Tool failed',
    })
  })
})
