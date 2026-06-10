import { afterEach, describe, expect, it, vi } from 'vitest'

import { openSseStream } from './sse'

// openSseStream goes through the shared apiFetch wrapper (CSRF, error
// shaping) — mock it so tests feed raw byte chunks straight into the
// SSE parser.
vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(),
}))

import { apiFetch } from '@/lib/api'

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream)
}

async function collect(chunks: string[]) {
  vi.mocked(apiFetch).mockResolvedValueOnce(responseFromChunks(chunks))
  const events = []
  for await (const event of openSseStream('/chat/stream', {})) {
    events.push(event)
  }
  return events
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('openSseStream', () => {
  it('parses one event per data frame', async () => {
    const events = await collect([
      'data: {"type": "text", "text": "hello"}\n\n',
      'data: {"type": "done"}\n\n',
    ])
    expect(events).toEqual([
      { type: 'text', text: 'hello' },
      { type: 'done' },
    ])
  })

  it('reassembles events split across network chunks', async () => {
    const events = await collect([
      'data: {"type": "te',
      'xt", "text": "ab',
      'c"}\n\ndata: {"type": "done"}\n\n',
    ])
    expect(events).toEqual([
      { type: 'text', text: 'abc' },
      { type: 'done' },
    ])
  })

  it('ignores comment frames (heartbeat pings)', async () => {
    const events = await collect([
      ': ping\n\n',
      'data: {"type": "done"}\n\n',
      ': ping\n\n',
    ])
    expect(events).toEqual([{ type: 'done' }])
  })

  it('drops malformed JSON without killing the stream', async () => {
    const events = await collect([
      'data: {not json}\n\n',
      'data: {"type": "done"}\n\n',
    ])
    expect(events).toEqual([{ type: 'done' }])
  })

  it('handles \\r\\n\\r\\n record separators', async () => {
    const events = await collect([
      'data: {"type": "text", "text": "a"}\r\n\r\ndata: {"type": "done"}\r\n\r\n',
    ])
    expect(events).toEqual([
      { type: 'text', text: 'a' },
      { type: 'done' },
    ])
  })

  it('joins multi-line data fields with newlines per the SSE spec', async () => {
    const events = await collect([
      'data: {"type": "text",\ndata: "text": "x"}\n\n',
    ])
    expect(events).toEqual([{ type: 'text', text: 'x' }])
  })

  it('throws when the response has no body', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(null))
    const iterate = async () => {
      for await (const _ of openSseStream('/chat/stream', {})) void _
    }
    await expect(iterate()).rejects.toThrow('Stream response has no body')
  })
})
