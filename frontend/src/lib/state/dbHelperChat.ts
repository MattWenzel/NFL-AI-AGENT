/**
 * `useDbHelperChat()` — local-only chat state for the Database browser's
 * SQL helper. Holds the wire message list in `useReducer` and streams
 * one turn at a time via `openSseStream` against `/database/helper-chat/stream`.
 *
 * No global context, no `chatStore` integration — refresh wipes the
 * history by design.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react'

import { ApiError } from '@/lib/api'
import { openSseStream } from '@/lib/api/sse'

export interface UseDbHelperChatOptions {
  /** Fired when the agent calls the `run_in_editor` tool — the helper is
   *  asking the Database view to push this SQL into its editor and run it.
   *  Optional; if not set, the tool result still streams as a normal
   *  collapsible tool detail in the chat. */
  onRunInEditor?: (sql: string) => void
}

export interface HelperToolCall {
  id: string
  name: string
  input: Record<string, unknown>
}

export interface HelperToolRun {
  /** Matches `tool_use_id` on the wire — used to pair with the result. */
  id: string
  name: string
  input: Record<string, unknown>
  /** `null` while waiting for the tool result; the JSON-string body once it lands. */
  content: string | null
  status: 'pending' | 'completed' | 'failed'
  /** Server-reported error message when the tool failed. */
  error: string | null
}

export type HelperRole = 'user' | 'assistant'

/** Message variant the slim renderer consumes. Tool calls + their results
 *  are folded onto the assistant message so the UI can render them as
 *  collapsible details under the matching turn — the wire still treats
 *  them as separate `tool_result` messages, which is what `toWireMessages`
 *  produces when it's time to POST. */
export interface HelperMessage {
  role: HelperRole
  text: string
  /** Only set for assistant messages. */
  toolRuns?: HelperToolRun[]
}

interface HelperState {
  messages: HelperMessage[]
  streaming: boolean
  error: string | null
}

type HelperAction =
  | { type: 'send'; text: string }
  | { type: 'text'; text: string }
  | { type: 'tool-call'; call: HelperToolCall }
  | { type: 'tool-result'; tool_run_id: string; content: string }
  | { type: 'tool-failed'; tool_run_id: string; content: string; message: string }
  | { type: 'error'; message: string }
  | { type: 'done' }
  | { type: 'clear' }
  | { type: 'rollback-last' }  // user aborts; drop the in-flight assistant msg

const initialState: HelperState = {
  messages: [],
  streaming: false,
  error: null,
}

function lastIndex(messages: HelperMessage[]): number {
  return messages.length - 1
}

function reducer(state: HelperState, action: HelperAction): HelperState {
  switch (action.type) {
    case 'send': {
      // Append the user message + a placeholder assistant message that
      // streaming events fill in. Two messages per send keeps the
      // rendering trivially in order.
      return {
        ...state,
        streaming: true,
        error: null,
        messages: [
          ...state.messages,
          { role: 'user', text: action.text },
          { role: 'assistant', text: '', toolRuns: [] },
        ],
      }
    }
    case 'text': {
      const i = lastIndex(state.messages)
      if (i < 0 || state.messages[i].role !== 'assistant') return state
      const next = [...state.messages]
      next[i] = { ...next[i], text: next[i].text + action.text }
      return { ...state, messages: next }
    }
    case 'tool-call': {
      const i = lastIndex(state.messages)
      if (i < 0 || state.messages[i].role !== 'assistant') return state
      const existing = state.messages[i].toolRuns ?? []
      const next = [...state.messages]
      next[i] = {
        ...next[i],
        toolRuns: [
          ...existing,
          {
            id: action.call.id,
            name: action.call.name,
            input: action.call.input,
            content: null,
            status: 'pending',
            error: null,
          },
        ],
      }
      return { ...state, messages: next }
    }
    case 'tool-result':
    case 'tool-failed': {
      const i = lastIndex(state.messages)
      if (i < 0 || state.messages[i].role !== 'assistant') return state
      const runs = state.messages[i].toolRuns ?? []
      const updatedRuns = runs.map((r) =>
        r.id === action.tool_run_id
          ? {
              ...r,
              content: action.content,
              status: action.type === 'tool-result' ? ('completed' as const) : ('failed' as const),
              error: action.type === 'tool-failed' ? action.message : null,
            }
          : r,
      )
      const next = [...state.messages]
      next[i] = { ...next[i], toolRuns: updatedRuns }
      return { ...state, messages: next }
    }
    case 'error':
      return { ...state, streaming: false, error: action.message }
    case 'done':
      return { ...state, streaming: false }
    case 'clear':
      return { ...initialState }
    case 'rollback-last': {
      // Drop the trailing user+assistant pair if streaming was aborted
      // before any text/tool events arrived. Keeps the history clean so
      // the next send doesn't repeat the rolled-back user message.
      const i = lastIndex(state.messages)
      if (i < 1) return { ...state, streaming: false }
      const last = state.messages[i]
      if (last.role !== 'assistant') return { ...state, streaming: false }
      const empty = !last.text && (!last.toolRuns || last.toolRuns.length === 0)
      if (!empty) return { ...state, streaming: false }
      const trimmed = state.messages.slice(0, i - 1) // drop user + assistant
      return { ...state, messages: trimmed, streaming: false }
    }
    default:
      return state
  }
}

/** Convert the local view-model into the wire shape the backend expects.
 *  Tool calls + results live on assistant messages in the view-model;
 *  on the wire, the tool result is its own message after the assistant
 *  call. Mirrors the provider's canonical message format. */
function toWireMessages(messages: HelperMessage[]): unknown[] {
  const out: unknown[] = []
  for (const m of messages) {
    if (m.role === 'user') {
      out.push({ role: 'user', text: m.text })
      continue
    }
    const toolCalls = (m.toolRuns ?? []).map((r) => ({
      id: r.id,
      name: r.name,
      input: r.input,
    }))
    out.push({
      role: 'assistant',
      text: m.text || null,
      tool_calls: toolCalls.length > 0 ? toolCalls : null,
    })
    for (const r of m.toolRuns ?? []) {
      if (r.content == null) continue // skip tools that never resolved
      out.push({
        role: 'tool_result',
        tool_use_id: r.id,
        content: r.content,
      })
    }
  }
  return out
}

export interface HelperSendOptions {
  provider: string
  model: string
  toolChoice: 'auto' | 'required' | 'none'
}

export function useDbHelperChat(options: UseDbHelperChatOptions = {}) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const abortRef = useRef<AbortController | null>(null)
  // Always-fresh ref so `send` can read the latest message list when
  // building the wire payload (otherwise the closure would capture the
  // pre-dispatch state and miss the user message we just queued).
  const messagesRef = useRef(state.messages)
  messagesRef.current = state.messages

  // Always-fresh ref to the run-in-editor callback so we don't need to
  // re-create `send` every time the parent re-renders with a new closure.
  const onRunInEditorRef = useRef(options.onRunInEditor)
  useEffect(() => {
    onRunInEditorRef.current = options.onRunInEditor
  }, [options.onRunInEditor])

  const send = useCallback(
    async (text: string, opts: HelperSendOptions) => {
      const trimmed = text.trim()
      if (!trimmed || abortRef.current) return

      dispatch({ type: 'send', text: trimmed })
      // After the dispatch, messagesRef will be updated on the next
      // render. Build the wire body from the not-yet-rendered next state
      // by appending the user message ourselves.
      const wire = toWireMessages([
        ...messagesRef.current,
        { role: 'user', text: trimmed },
      ])

      const controller = new AbortController()
      abortRef.current = controller

      try {
        for await (const event of openSseStream(
          '/database/helper-chat/stream',
          {
            messages: wire,
            provider: opts.provider,
            model: opts.model,
            tool_choice: opts.toolChoice,
          },
          { signal: controller.signal },
        )) {
          switch (event.type) {
            case 'text':
              if (typeof event.text === 'string') {
                dispatch({ type: 'text', text: event.text })
              }
              break
            case 'tool_call':
              dispatch({
                type: 'tool-call',
                call: {
                  id: String(event.tool_run_id),
                  name: String(event.name),
                  input: (event.input as Record<string, unknown>) ?? {},
                },
              })
              break
            case 'tool_result':
              dispatch({
                type: 'tool-result',
                tool_run_id: String(event.tool_run_id),
                content: typeof event.content === 'string' ? event.content : '',
              })
              // `run_in_editor` is a remote-control tool — when it
              // succeeds, hand the SQL off to the Database view so it
              // populates the editor and runs the query.
              if (
                event.name === 'run_in_editor' &&
                typeof event.content === 'string' &&
                onRunInEditorRef.current
              ) {
                try {
                  const parsed = JSON.parse(event.content) as { sql?: unknown }
                  if (typeof parsed.sql === 'string' && parsed.sql.trim()) {
                    onRunInEditorRef.current(parsed.sql)
                  }
                } catch {
                  // Malformed payload — drop silently.
                }
              }
              break
            case 'tool_failed':
              dispatch({
                type: 'tool-failed',
                tool_run_id: String(event.tool_run_id),
                content: typeof event.content === 'string' ? event.content : '',
                message: typeof event.message === 'string' ? event.message : 'Tool failed',
              })
              break
            case 'error':
              dispatch({
                type: 'error',
                message: typeof event.message === 'string' ? event.message : 'Stream error',
              })
              break
            case 'done':
              dispatch({ type: 'done' })
              break
          }
        }
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') {
          dispatch({ type: 'rollback-last' })
        } else {
          const message =
            err instanceof ApiError
              ? err.detail
              : err instanceof Error
                ? err.message
                : 'Stream failed'
          dispatch({ type: 'error', message })
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        if (state.streaming) dispatch({ type: 'done' })
      }
    },
    [state.streaming],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const clear = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    dispatch({ type: 'clear' })
  }, [])

  return {
    messages: state.messages,
    streaming: state.streaming,
    error: state.error,
    send,
    stop,
    clear,
  }
}
