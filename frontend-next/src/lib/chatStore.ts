import { useCallback, useEffect, useReducer, useRef } from 'react'

import { apiDelete, apiGet, apiPatch, ApiError } from '@/lib/api'
import { openSseStream } from '@/lib/sse'
import type {
  AssistantPartRecord,
  ConversationInfo,
  ConversationTranscript,
  ToolRunRecord,
  TurnRecord,
} from '@/lib/types'

export type StreamStatus = 'idle' | 'streaming' | 'error'

export interface ChatState {
  conversationId: string | null
  transcript: ConversationTranscript | null
  conversations: ConversationInfo[]
  conversationsStatus: 'idle' | 'loading' | 'ready' | 'error'
  streamStatus: StreamStatus
  streamError: string | null
}

type Action =
  | { type: 'set-conversations'; conversations: ConversationInfo[] }
  | { type: 'conversations-loading' }
  | { type: 'conversations-error' }
  | { type: 'set-transcript'; transcript: ConversationTranscript | null }
  | { type: 'new-conversation' }
  | { type: 'optimistic-user-turn'; turn: TurnRecord }
  | { type: 'set-conversation-id'; id: string }
  | { type: 'assistant-started'; turnId: string }
  | { type: 'text-delta'; text: string }
  | { type: 'tool-call'; toolRunId: string; name: string; input: Record<string, unknown> }
  | { type: 'tool-result'; toolRunId: string; status: 'completed' | 'error'; message?: string }
  | { type: 'stream-error'; message: string }
  | { type: 'stream-done' }

function emptyTranscript(id: string | null): ConversationTranscript {
  return {
    session_id: id ?? '',
    title: null,
    provider: null,
    model: null,
    updated_at: null,
    turns: [],
    parts: [],
    tool_runs: [],
    summaries: [],
  }
}

function lastAssistantTurnId(t: ConversationTranscript): string | null {
  for (let i = t.turns.length - 1; i >= 0; i--) {
    if (t.turns[i].role === 'assistant') return t.turns[i].id
  }
  return null
}

/**
 * The backend persists each text-delta as its own AssistantPartRecord, so a
 * single agent paragraph arrives as N small parts in `order_index` order. If
 * we render each one as its own block, prose staircases vertically and any
 * markdown table that spans multiple deltas falls apart at the slice
 * boundaries. Merge consecutive same-turn text parts into one before
 * rendering — tool_use parts break the run, which is correct (they delimit
 * agent reasoning around tool calls).
 */
function mergeTextParts(transcript: ConversationTranscript): ConversationTranscript {
  const byTurn = new Map<string, AssistantPartRecord[]>()
  for (const p of transcript.parts) {
    const list = byTurn.get(p.turn_id) ?? []
    list.push(p)
    byTurn.set(p.turn_id, list)
  }
  const merged: AssistantPartRecord[] = []
  for (const list of byTurn.values()) {
    list.sort((a, b) => a.order_index - b.order_index)
    let bucket: AssistantPartRecord | null = null
    for (const p of list) {
      if (p.kind === 'text' && bucket !== null && bucket.kind === 'text') {
        const prev: AssistantPartRecord = bucket
        bucket = { ...prev, content: prev.content + p.content }
      } else {
        if (bucket !== null) merged.push(bucket)
        bucket = { ...p }
      }
    }
    if (bucket !== null) merged.push(bucket)
  }
  return { ...transcript, parts: merged }
}

function reduce(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'conversations-loading':
      return { ...state, conversationsStatus: 'loading' }
    case 'set-conversations':
      return { ...state, conversations: action.conversations, conversationsStatus: 'ready' }
    case 'conversations-error':
      return { ...state, conversationsStatus: 'error' }
    case 'set-transcript': {
      const merged = action.transcript ? mergeTextParts(action.transcript) : null
      return {
        ...state,
        transcript: merged,
        conversationId: merged?.session_id ?? null,
        streamStatus: state.streamStatus === 'streaming' ? 'streaming' : 'idle',
        streamError: null,
      }
    }
    case 'new-conversation':
      return {
        ...state,
        transcript: null,
        conversationId: null,
        streamStatus: 'idle',
        streamError: null,
      }
    case 'optimistic-user-turn': {
      const transcript = state.transcript ?? emptyTranscript(state.conversationId)
      return {
        ...state,
        streamStatus: 'streaming',
        streamError: null,
        transcript: { ...transcript, turns: [...transcript.turns, action.turn] },
      }
    }
    case 'set-conversation-id': {
      const transcript = state.transcript ?? emptyTranscript(action.id)
      return {
        ...state,
        conversationId: action.id,
        transcript: { ...transcript, session_id: action.id },
      }
    }
    case 'assistant-started': {
      const transcript = state.transcript ?? emptyTranscript(state.conversationId)
      if (transcript.turns.some((t) => t.id === action.turnId)) return state
      const now = new Date().toISOString()
      const assistantTurn: TurnRecord = {
        id: action.turnId,
        role: 'assistant',
        status: 'streaming',
        text: '',
        compacted: false,
        error: null,
        input_tokens: 0,
        output_tokens: 0,
        created_at: now,
        updated_at: now,
      }
      return {
        ...state,
        transcript: { ...transcript, turns: [...transcript.turns, assistantTurn] },
      }
    }
    case 'text-delta': {
      if (!state.transcript) return state
      const turnId = lastAssistantTurnId(state.transcript)
      if (!turnId) return state
      const parts = [...state.transcript.parts]
      // Find the last part on this turn — extend if it's text, else push a new text part.
      let extendedExisting = false
      for (let i = parts.length - 1; i >= 0; i--) {
        if (parts[i].turn_id === turnId) {
          if (parts[i].kind === 'text') {
            parts[i] = { ...parts[i], content: parts[i].content + action.text }
            extendedExisting = true
          }
          break
        }
      }
      if (!extendedExisting) {
        const turnParts = parts.filter((p) => p.turn_id === turnId)
        parts.push({
          id: `streaming-text-${turnId}-${turnParts.length}`,
          turn_id: turnId,
          kind: 'text',
          order_index: turnParts.length,
          content: action.text,
          created_at: new Date().toISOString(),
        })
      }
      return { ...state, transcript: { ...state.transcript, parts } }
    }
    case 'tool-call': {
      if (!state.transcript) return state
      const turnId = lastAssistantTurnId(state.transcript)
      if (!turnId) return state
      const turnParts = state.transcript.parts.filter((p) => p.turn_id === turnId)
      const now = new Date().toISOString()
      const newPart: AssistantPartRecord = {
        id: `streaming-tool-${action.toolRunId}`,
        turn_id: turnId,
        kind: 'tool_use',
        order_index: turnParts.length,
        content: '',
        name: action.name,
        tool_run_id: action.toolRunId,
        created_at: now,
      }
      const newRun: ToolRunRecord = {
        id: action.toolRunId,
        turn_id: turnId,
        tool_name: action.name,
        input: action.input,
        status: 'running',
        result: null,
        error: null,
        hint: null,
        duration_ms: null,
        compacted: false,
        created_at: now,
        updated_at: now,
      }
      return {
        ...state,
        transcript: {
          ...state.transcript,
          parts: [...state.transcript.parts, newPart],
          tool_runs: [...state.transcript.tool_runs, newRun],
        },
      }
    }
    case 'tool-result': {
      if (!state.transcript) return state
      const tool_runs = state.transcript.tool_runs.map((r) =>
        r.id === action.toolRunId
          ? {
              ...r,
              status: action.status,
              error:
                action.status === 'error' ? action.message ?? r.error : r.error,
              updated_at: new Date().toISOString(),
            }
          : r,
      )
      return { ...state, transcript: { ...state.transcript, tool_runs } }
    }
    case 'stream-error':
      return { ...state, streamStatus: 'error', streamError: action.message }
    case 'stream-done':
      return { ...state, streamStatus: 'idle' }
    default:
      return state
  }
}

const INITIAL: ChatState = {
  conversationId: null,
  transcript: null,
  conversations: [],
  conversationsStatus: 'idle',
  streamStatus: 'idle',
  streamError: null,
}

export interface SendOptions {
  provider?: string
  model?: string
  toolChoice?: 'auto' | 'required' | 'none'
}

export function useChat() {
  const [state, dispatch] = useReducer(reduce, INITIAL)
  const stateRef = useRef(state)
  stateRef.current = state
  const abortRef = useRef<AbortController | null>(null)

  const refreshConversations = useCallback(async () => {
    dispatch({ type: 'conversations-loading' })
    try {
      const list = await apiGet<ConversationInfo[]>('/chat/conversations')
      dispatch({ type: 'set-conversations', conversations: list })
    } catch {
      dispatch({ type: 'conversations-error' })
    }
  }, [])

  const loadConversation = useCallback(async (id: string) => {
    try {
      const transcript = await apiGet<ConversationTranscript>(
        `/chat/conversations/${encodeURIComponent(id)}/transcript`,
      )
      dispatch({ type: 'set-transcript', transcript })
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Failed to load conversation'
      dispatch({ type: 'stream-error', message: msg })
    }
  }, [])

  const newConversation = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    dispatch({ type: 'new-conversation' })
  }, [])

  const setPinned = useCallback(async (id: string, pinned: boolean) => {
    try {
      await apiPatch<ConversationInfo>(
        `/chat/conversations/${encodeURIComponent(id)}`,
        { pinned },
      )
    } catch {
      // ignore — surfaced via list refresh
    }
    await refreshConversations()
  }, [refreshConversations])

  const removeConversation = useCallback(async (id: string) => {
    try {
      await apiDelete(`/chat/conversations/${encodeURIComponent(id)}`)
    } catch {
      // ignore — surfaced via list refresh
    }
    if (stateRef.current.conversationId === id) {
      dispatch({ type: 'new-conversation' })
    }
    await refreshConversations()
  }, [refreshConversations])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const send = useCallback(
    async (message: string, options: SendOptions = {}) => {
      if (stateRef.current.streamStatus === 'streaming') return
      const controller = new AbortController()
      abortRef.current = controller

      const optimisticTurn: TurnRecord = {
        id: `optimistic-user-${Date.now()}`,
        role: 'user',
        status: 'complete',
        text: message,
        compacted: false,
        error: null,
        input_tokens: 0,
        output_tokens: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      dispatch({ type: 'optimistic-user-turn', turn: optimisticTurn })

      const body: Record<string, unknown> = { message }
      if (stateRef.current.conversationId) body.conversation_id = stateRef.current.conversationId
      if (options.provider) body.provider = options.provider
      if (options.model) body.model = options.model
      if (options.toolChoice) body.tool_choice = options.toolChoice

      try {
        for await (const event of openSseStream('/chat/stream', body, { signal: controller.signal })) {
          switch (event.type) {
            case 'conversation_id':
              if (typeof event.id === 'string') {
                dispatch({ type: 'set-conversation-id', id: event.id })
              }
              break
            case 'assistant_started':
              if (typeof event.turn_id === 'string') {
                dispatch({ type: 'assistant-started', turnId: event.turn_id })
              }
              break
            case 'text':
              if (typeof event.text === 'string') {
                dispatch({ type: 'text-delta', text: event.text })
              }
              break
            case 'tool_call':
              if (typeof event.tool_run_id === 'string' && typeof event.name === 'string') {
                dispatch({
                  type: 'tool-call',
                  toolRunId: event.tool_run_id,
                  name: event.name,
                  input: (event.input as Record<string, unknown>) ?? {},
                })
              }
              break
            case 'tool_result':
              if (typeof event.tool_run_id === 'string') {
                dispatch({ type: 'tool-result', toolRunId: event.tool_run_id, status: 'completed' })
              }
              break
            case 'tool_failed':
              if (typeof event.tool_run_id === 'string') {
                dispatch({
                  type: 'tool-result',
                  toolRunId: event.tool_run_id,
                  status: 'error',
                  message: typeof event.message === 'string' ? event.message : undefined,
                })
              }
              break
            case 'error':
              dispatch({
                type: 'stream-error',
                message: typeof event.message === 'string' ? event.message : 'Stream error',
              })
              break
            case 'done':
              break
            default:
              break
          }
        }

        // Stream finished cleanly — pull authoritative transcript so tool
        // results, token counts, and stable IDs replace the optimistic shape.
        const settledId = stateRef.current.conversationId
        if (settledId) {
          try {
            const transcript = await apiGet<ConversationTranscript>(
              `/chat/conversations/${encodeURIComponent(settledId)}/transcript`,
            )
            dispatch({ type: 'set-transcript', transcript })
          } catch {
            // Keep the optimistic state if the refetch fails.
          }
          await refreshConversations()
        }
        dispatch({ type: 'stream-done' })
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') {
          dispatch({ type: 'stream-done' })
          return
        }
        const msg = e instanceof ApiError ? e.detail : (e as Error)?.message ?? 'Stream failed'
        dispatch({ type: 'stream-error', message: msg })
      } finally {
        if (abortRef.current === controller) abortRef.current = null
      }
    },
    [refreshConversations],
  )

  // Load the conversation list on mount (best effort — silently fails if unauthenticated).
  useEffect(() => {
    refreshConversations()
  }, [refreshConversations])

  return {
    ...state,
    refreshConversations,
    loadConversation,
    newConversation,
    setPinned,
    removeConversation,
    send,
    stop,
  }
}
