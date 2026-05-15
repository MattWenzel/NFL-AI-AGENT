/**
 * SSE event → typed-handler dispatch, shared by `chatStore` and
 * `dbHelperChat`.
 *
 * Both stores open the same kind of SSE stream against the backend and
 * walk a `switch (event.type)` block, parsing fields with the same
 * `typeof event.x === 'string'` style guards before dispatching to
 * their own reducer. The wire shape (event-type names + field types)
 * was duplicated across both stores, so adding a new event type — or
 * tightening a field's parsing — meant patching two places.
 *
 * This module concentrates the wire-format knowledge. Each store
 * passes a `StreamHandlers` object with callbacks for the event types
 * it cares about; unhandled events are silently ignored. Lifecycle
 * concerns that *aren't* uniform across the two stores (optimistic
 * user turns, abort handling, post-stream transcript refetch) stay in
 * each store where they belong.
 */

import { openSseStream, type SseEvent } from '@/lib/api/sse'

export interface StreamHandlers {
  /** Token-by-token assistant text. */
  onText?: (text: string) => void
  /** Provider transient-error retry notice. */
  onRetrying?: (payload: { attempt: number; delay_seconds: number; message: string }) => void
  /** Model emitted a tool_use; tool hasn't run yet. */
  onToolCall?: (call: { tool_run_id: string; name: string; input: Record<string, unknown> }) => void
  /** Tool finished successfully. `content` is the JSON-stringified tool result body. */
  onToolResult?: (payload: { tool_run_id: string; name: string; content: string }) => void
  /** Tool finished with an error envelope. */
  onToolFailed?: (payload: {
    tool_run_id: string
    name: string
    content: string
    message: string
  }) => void
  /** Persistent runtime stamped the assistant turn's id (chat only). */
  onAssistantStarted?: (turnId: string) => void
  /** Conversation id resolved by the persistent runtime (chat only). */
  onConversationId?: (id: string) => void
  /** Live table state changed (table_chat sessions). */
  onTableUpdated?: (payload: {
    tool_run_id: string
    row_count: number
    truncated: boolean
    columns: string[]
  }) => void
  /** New Report session was minted by the agent (chat only). */
  onReportCreated?: (payload: {
    tool_run_id: string
    report_id: string
    title: string
    row_count: number
  }) => void
  /** Server emitted an error envelope on the stream (versus the connection failing). */
  onError?: (message: string) => void
  /** Stream finished cleanly. */
  onDone?: () => void
}

export interface StreamDispatchOptions {
  signal?: AbortSignal
}

/**
 * Open `path` as an SSE stream, parse each event, and call the matching
 * handler. Returns when the stream closes. Unhandled event types are
 * silently dropped — callers opt in by providing the relevant callback.
 */
export async function streamAndDispatch(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  options: StreamDispatchOptions = {},
): Promise<void> {
  for await (const event of openSseStream(path, body, { signal: options.signal })) {
    dispatchEvent(event, handlers)
  }
}

function dispatchEvent(event: SseEvent, h: StreamHandlers): void {
  switch (event.type) {
    case 'text':
      if (typeof event.text === 'string') h.onText?.(event.text)
      return
    case 'retrying':
      if (
        typeof event.attempt === 'number' &&
        typeof event.delay_seconds === 'number'
      ) {
        h.onRetrying?.({
          attempt: event.attempt,
          delay_seconds: event.delay_seconds,
          message: typeof event.message === 'string' ? event.message : '',
        })
      }
      return
    case 'tool_call':
      if (typeof event.tool_run_id === 'string' && typeof event.name === 'string') {
        h.onToolCall?.({
          tool_run_id: event.tool_run_id,
          name: event.name,
          input: (event.input as Record<string, unknown>) ?? {},
        })
      }
      return
    case 'tool_result':
      if (typeof event.tool_run_id === 'string') {
        h.onToolResult?.({
          tool_run_id: event.tool_run_id,
          name: typeof event.name === 'string' ? event.name : '',
          content: typeof event.content === 'string' ? event.content : '',
        })
      }
      return
    case 'tool_failed':
      if (typeof event.tool_run_id === 'string') {
        h.onToolFailed?.({
          tool_run_id: event.tool_run_id,
          name: typeof event.name === 'string' ? event.name : '',
          content: typeof event.content === 'string' ? event.content : '',
          message: typeof event.message === 'string' ? event.message : 'Tool failed',
        })
      }
      return
    case 'assistant_started':
      if (typeof event.turn_id === 'string') h.onAssistantStarted?.(event.turn_id)
      return
    case 'conversation_id':
      if (typeof event.id === 'string') h.onConversationId?.(event.id)
      return
    case 'table_updated':
      if (
        typeof event.tool_run_id === 'string' &&
        typeof event.row_count === 'number' &&
        Array.isArray(event.columns)
      ) {
        h.onTableUpdated?.({
          tool_run_id: event.tool_run_id,
          row_count: event.row_count,
          truncated: Boolean(event.truncated),
          columns: (event.columns as unknown[]).map(String),
        })
      }
      return
    case 'report_created':
      if (typeof event.report_id === 'string' && typeof event.tool_run_id === 'string') {
        h.onReportCreated?.({
          tool_run_id: event.tool_run_id,
          report_id: event.report_id,
          title: typeof event.title === 'string' ? event.title : '',
          row_count: typeof event.row_count === 'number' ? event.row_count : 0,
        })
      }
      return
    case 'error':
      h.onError?.(typeof event.message === 'string' ? event.message : 'Stream error')
      return
    case 'done':
      h.onDone?.()
      return
    default:
      return
  }
}
