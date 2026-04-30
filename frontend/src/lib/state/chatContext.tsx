import { createContext, useContext, type ReactNode } from 'react'

import { useChat } from '@/lib/state/chatStore'

type ChatContextValue = ReturnType<typeof useChat>

const ChatContext = createContext<ChatContextValue | null>(null)

export function ChatProvider({ children }: { children: ReactNode }) {
  const value = useChat()
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export function useChatContext() {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChatContext must be used inside <ChatProvider>')
  return ctx
}
