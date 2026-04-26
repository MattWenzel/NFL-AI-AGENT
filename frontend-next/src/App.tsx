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
import { SettingsModal } from '@/components/settings/SettingsModal'
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

  return (
    <>
      <AppShell
        sidebar={
          <Sidebar
            user={user}
            onLogout={onLogout}
            onOpenSettings={() => setSettingsOpen(true)}
            onNewChat={chat.newConversation}
            onOpenConversation={chat.loadConversation}
          />
        }
        inspector={<Inspector />}
        main={
          <>
            {chat.transcript && chat.transcript.turns.length > 0 ? (
              <Thread transcript={chat.transcript} />
            ) : (
              <EmptyThread />
            )}
            {chat.streamError ? (
              <div className="border-t border-destructive/30 bg-destructive/5 px-4 py-2 text-center text-xs text-destructive">
                {chat.streamError}
              </div>
            ) : null}
            <Composer
              streaming={chat.streamStatus === 'streaming'}
              onSend={(message, opts) => chat.send(message, opts)}
              onStop={chat.stop}
            />
          </>
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
    </>
  )
}
