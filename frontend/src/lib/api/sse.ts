/**
 * POST-SSE stream reader. The backend's `/chat/stream` is a POST endpoint
 * that returns `text/event-stream`, so EventSource (GET-only) doesn't fit;
 * we read the response body as a stream and parse SSE chunks manually.
 *
 * Each SSE event is a sequence of `field: value` lines terminated by a
 * blank line. We only care about the `data:` field — the chat protocol
 * never uses `event:` or `id:` framing.
 */

import { apiFetch } from '@/lib/api'

export type SseEvent = Record<string, unknown> & { type: string }

export interface OpenStreamOptions {
  signal?: AbortSignal
}

export async function* openSseStream(
  path: string,
  body: unknown,
  options: OpenStreamOptions = {},
): AsyncGenerator<SseEvent, void, undefined> {
  const res = await apiFetch(path, {
    method: 'POST',
    body: body == null ? undefined : JSON.stringify(body),
    signal: options.signal,
    headers: { Accept: 'text/event-stream' },
  })

  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('Stream response has no body')
  }
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Split on the SSE record separator. \r\n\r\n / \n\n / \r\r are all valid.
      let sepIndex: number
      while ((sepIndex = findSeparator(buffer)) !== -1) {
        const raw = buffer.slice(0, sepIndex)
        buffer = buffer.slice(sepIndex + separatorLength(buffer, sepIndex))
        const data = parseEventDataLines(raw)
        if (data === null) continue
        try {
          yield JSON.parse(data) as SseEvent
        } catch {
          // Malformed payload — ignore the event rather than killing the stream.
        }
      }
    }
  } finally {
    try {
      reader.cancel()
    } catch {
      // ignore — already closed
    }
  }
}

function findSeparator(buffer: string): number {
  const idxA = buffer.indexOf('\n\n')
  const idxB = buffer.indexOf('\r\n\r\n')
  if (idxA === -1) return idxB
  if (idxB === -1) return idxA
  return Math.min(idxA, idxB)
}

function separatorLength(buffer: string, idx: number): number {
  return buffer.startsWith('\r\n\r\n', idx) ? 4 : 2
}

function parseEventDataLines(record: string): string | null {
  const lines = record.split(/\r?\n/)
  const dataParts: string[] = []
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('data:')) {
      const v = line.slice(5)
      dataParts.push(v.startsWith(' ') ? v.slice(1) : v)
    }
  }
  if (dataParts.length === 0) return null
  return dataParts.join('\n')
}
