import { AppShell } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { Composer } from '@/components/composer/Composer'
import { Thread } from '@/components/thread/Thread'
import { EmptyThread } from '@/components/thread/EmptyThread'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { Toaster } from '@/components/ui/sonner'
import { AuthWall } from '@/components/auth/AuthWall'
import { useAuth } from '@/lib/auth'
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

function ChatWorkspace({ user, onLogout }: { user: import('@/lib/auth').AuthUser; onLogout: () => void }) {
  const chat = useChatContext()

  return (
    <AppShell
      sidebar={
        <Sidebar
          user={user}
          onLogout={onLogout}
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
          <Composer
            streaming={chat.streamStatus === 'streaming'}
            onSend={(message, opts) => chat.send(message, opts)}
            onStop={chat.stop}
          />
        </>
      }
    />
  )
}
