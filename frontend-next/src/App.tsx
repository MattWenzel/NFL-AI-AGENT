import { useState } from 'react'

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
          <ChatWorkspace user={auth.state.user} onLogout={auth.logout} />
        </ChatProvider>
      )}
      <Toaster position="bottom-right" />
    </ThemeProvider>
  )
}

function ChatWorkspace({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const chat = useChatContext()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [activeExportId, setActiveExportId] = useState<string | null>(null)

  const openExport = (id: string) => {
    setActiveExportId(id)
  }
  const closeExport = () => setActiveExportId(null)

  const openConversation = (id: string) => {
    setActiveExportId(null)
    chat.loadConversation(id)
  }

  return (
    <>
      <AppShell
        inspectorAvailable={!!chat.transcript && chat.transcript.turns.length > 0}
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
