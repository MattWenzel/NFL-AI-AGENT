import { useEffect, useRef, useState } from 'react'

import { AppShell } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { Composer } from '@/components/composer/Composer'
import { Thread } from '@/components/thread/Thread'
import { EmptyThread } from '@/components/thread/EmptyThread'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { Toaster } from '@/components/ui/sonner'
import { AuthWall } from '@/components/auth/AuthWall'
import { CommandPalette } from '@/components/command/CommandPalette'
import { SettingsModal } from '@/components/settings/SettingsModal'
import { CsvViewer } from '@/components/exports/CsvViewer'
import { useAuth, type AuthUser } from '@/lib/auth'
import { ChatProvider, useChatContext } from '@/lib/chatContext'
import { ExportsProvider } from '@/lib/exportsContext'

export default function App() {
  const auth = useAuth()

  return (
    <ThemeProvider>
      {auth.state.status === 'loading' ? (
        <div className="grid min-h-dvh place-items-center bg-background">
          <p className="text-sm text-muted-foreground">Loading…</p>
        </div>
      ) : auth.state.status === 'anonymous' ? (
        <AuthWall onLogin={auth.login} onRegister={auth.register} errorMessage={auth.state.error} />
      ) : (
        <ChatProvider>
          <ExportsProvider>
            <ChatWorkspace user={auth.state.user} onLogout={auth.logout} />
          </ExportsProvider>
        </ChatProvider>
      )}
      <Toaster position="bottom-right" />
    </ThemeProvider>
  )
}

const VIEW_KEY_PREFIX = 'nfl-stats:last-view:'

type LastView = { kind: 'chat' | 'report'; id: string }

function readLastView(userId: number): LastView | null {
  try {
    const raw = localStorage.getItem(VIEW_KEY_PREFIX + userId)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<LastView>
    if (
      parsed &&
      (parsed.kind === 'chat' || parsed.kind === 'report') &&
      typeof parsed.id === 'string'
    ) {
      return { kind: parsed.kind, id: parsed.id }
    }
  } catch {
    // localStorage may be unavailable (private mode) or hold corrupted JSON.
  }
  return null
}

function writeLastView(userId: number, v: LastView | null) {
  try {
    if (v) localStorage.setItem(VIEW_KEY_PREFIX + userId, JSON.stringify(v))
    else localStorage.removeItem(VIEW_KEY_PREFIX + userId)
  } catch {
    // Quota or availability errors are non-fatal — refresh-restore is a
    // nice-to-have, not a correctness requirement.
  }
}

function ChatWorkspace({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const chat = useChatContext()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Read the saved view synchronously so the very first render already
  // reflects "you were on a report" or "you were on a chat" — otherwise the
  // EmptyThread flashes before the restore effect runs.
  const initialView = useRef<LastView | null>(readLastView(user.id))
  const [activeExportId, setActiveExportId] = useState<string | null>(
    initialView.current?.kind === 'report' ? initialView.current.id : null,
  )
  const [restoringChat, setRestoringChat] = useState<boolean>(
    initialView.current?.kind === 'chat',
  )
  const didRestoreRef = useRef(false)

  // Kick off the chat-transcript fetch; the lazy initial state above already
  // suppressed the new-chat flash, this just resolves the loading gate once
  // the transcript arrives (or fails).
  useEffect(() => {
    if (didRestoreRef.current) return
    didRestoreRef.current = true
    const v = initialView.current
    if (v?.kind !== 'chat') return
    chat.loadConversation(v.id).finally(() => setRestoringChat(false))
    // Restore must run exactly once per session; capturing chat in deps
    // would re-trigger on every render since the context value is fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id])

  // Persist whichever view is currently active.
  useEffect(() => {
    if (activeExportId) {
      writeLastView(user.id, { kind: 'report', id: activeExportId })
    } else if (chat.conversationId) {
      writeLastView(user.id, { kind: 'chat', id: chat.conversationId })
    } else {
      writeLastView(user.id, null)
    }
  }, [user.id, activeExportId, chat.conversationId])

  const openExport = (id: string) => {
    setActiveExportId(id)
  }
  const closeExport = () => setActiveExportId(null)

  const openConversation = (id: string) => {
    setActiveExportId(null)
    chat.loadConversation(id)
  }

  const switchToChats = () => {
    setActiveExportId(null)
    // If no chat is currently loaded but the user has prior conversations,
    // jump to the most recent one so "Chats" never lands on a dead empty
    // state when they had history.
    if (!chat.transcript && chat.conversations.length > 0) {
      chat.loadConversation(chat.conversations[0].id)
    }
  }

  return (
    <>
      <AppShell
        inspectorAvailable={
          !activeExportId && !!chat.transcript && chat.transcript.turns.length > 0
        }
        sidebar={
          <Sidebar
            user={user}
            onLogout={onLogout}
            onOpenSettings={() => setSettingsOpen(true)}
            onNewChat={() => {
              setActiveExportId(null)
              chat.newConversation()
            }}
            onOpenConversation={openConversation}
            activeExportId={activeExportId}
            onOpenExport={openExport}
            onSwitchToChats={switchToChats}
          />
        }
        inspector={<Inspector />}
        main={
          activeExportId ? (
            <CsvViewer
              exportId={activeExportId}
              onClose={closeExport}
              onConversationCreated={(conversationId) => {
                setActiveExportId(null)
                chat.loadConversation(conversationId)
              }}
            />
          ) : restoringChat ? (
            // Blank pane during refresh-restore so the EmptyThread headline
            // doesn't flash before the transcript arrives.
            <div className="flex-1" />
          ) : chat.transcript && chat.transcript.turns.length > 0 ? (
            <>
              <Thread transcript={chat.transcript} />
              {chat.streamError ? (
                <div className="px-4 py-2 text-center text-xs text-destructive">
                  {chat.streamError}
                </div>
              ) : null}
              <Composer
                variant="docked"
                streaming={chat.streamStatus === 'streaming'}
                onSend={(message, opts) => chat.send(message, opts)}
                onStop={chat.stop}
              />
            </>
          ) : (
            <EmptyThread>
              <Composer
                variant="centered"
                streaming={chat.streamStatus === 'streaming'}
                onSend={(message, opts) => chat.send(message, opts)}
                onStop={chat.stop}
              />
            </EmptyThread>
          )
        }
      />
      <SettingsModal
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        user={user}
        onAccountDeleted={() => {
          setSettingsOpen(false)
          onLogout()
        }}
      />
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onNewChat={() => {
          setActiveExportId(null)
          chat.newConversation()
        }}
        onPickConversation={openConversation}
        onOpenSettings={() => setSettingsOpen(true)}
        onSignOut={onLogout}
      />
    </>
  )
}
