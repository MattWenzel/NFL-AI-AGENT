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
import { TableChatView } from '@/components/tables/TableChatView'
import { EmptyReportScreen } from '@/components/tables/EmptyReportScreen'
import { useAuth, type AuthUser } from '@/lib/auth'
import { ChatProvider, useChatContext } from '@/lib/chatContext'
import { TablesProvider, useTablesContext } from '@/lib/tablesContext'
import type { ConversationInfo } from '@/lib/types'

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
          <TablesProvider>
            <ChatWorkspace user={auth.state.user} onLogout={auth.logout} />
          </TablesProvider>
        </ChatProvider>
      )}
      <Toaster position="bottom-right" />
    </ThemeProvider>
  )
}

const VIEW_KEY_PREFIX = 'nfl-stats:last-view:'

// `kind: 'report'` here means a Report (the live editable table) — not the
// old read-only CSV library, which has been removed.
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

/** Pick the conversation with the latest `updated_at`, ignoring pin
 *  status. The sidebar list sorts pinned items first, so taking [0]
 *  doesn't give the actual most-recent chat when any pins exist. */
function mostRecentConversation(items: ConversationInfo[]): ConversationInfo | null {
  let best: ConversationInfo | null = null
  for (const c of items) {
    if (!c.updated_at) continue
    if (!best || (best.updated_at ?? '') < c.updated_at) best = c
  }
  return best ?? items[0] ?? null
}

function ChatWorkspace({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const chat = useChatContext()
  const tables = useTablesContext()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Read the saved view synchronously so the very first render already
  // reflects "you were on a report"/"chat" — otherwise the EmptyThread
  // headline flashes before the restore effect runs.
  const initialView = useRef<LastView | null>(readLastView(user.id))
  const [activeTableId, setActiveTableId] = useState<string | null>(
    initialView.current?.kind === 'report' ? initialView.current.id : null,
  )
  const [restoringChat, setRestoringChat] = useState<boolean>(
    initialView.current?.kind === 'chat' || initialView.current?.kind === 'report',
  )
  // True between clicking "New report" and the user sending the first
  // message. While true the empty-report screen is shown and no
  // /chat/tables session has been created yet.
  const [pendingReport, setPendingReport] = useState(false)
  const didRestoreRef = useRef(false)

  // Kick off the chat-transcript fetch; the lazy initial state above already
  // suppressed the new-chat flash, this just resolves the loading gate once
  // the transcript arrives (or fails).
  useEffect(() => {
    if (didRestoreRef.current) return
    didRestoreRef.current = true
    const v = initialView.current
    if (v?.kind !== 'chat' && v?.kind !== 'report') return
    chat.loadConversation(v.id).finally(() => setRestoringChat(false))
    // Restore must run exactly once per session; capturing chat in deps
    // would re-trigger on every render since the context value is fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id])

  // Persist whichever view is currently active.
  useEffect(() => {
    if (activeTableId) {
      writeLastView(user.id, { kind: 'report', id: activeTableId })
    } else if (chat.conversationId) {
      writeLastView(user.id, { kind: 'chat', id: chat.conversationId })
    } else {
      writeLastView(user.id, null)
    }
  }, [user.id, activeTableId, chat.conversationId])

  // If the active report disappears from the tables list (deleted from the
  // sidebar's 3-dot menu, the toolbar Delete, or another tab), bail out of
  // the report view and land on the empty new-report screen. Mirrors how
  // `chat.removeConversation` clears `chat.conversationId` when the deleted
  // id was the active one — but for tables, the active id lives here in
  // App.tsx, so the cleanup also lives here.
  useEffect(() => {
    if (!activeTableId) return
    if (tables.status !== 'ready') return
    if (tables.tables.some((t) => t.id === activeTableId)) return
    setActiveTableId(null)
    chat.newConversation()
    setPendingReport(true)
    // chat is stable (the context value's mutators are useCallback'd).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTableId, tables.status, tables.tables])

  const openConversation = (id: string) => {
    setActiveTableId(null)
    setPendingReport(false)
    chat.loadConversation(id)
  }

  const openTable = (id: string) => {
    setActiveTableId(id)
    setPendingReport(false)
    // The table-chat view reuses the chat transcript pipeline for the bottom
    // chat panel — load the same conversation so `chat.send` posts there.
    chat.loadConversation(id)
  }

  const switchToChats = () => {
    setActiveTableId(null)
    setPendingReport(false)
    // The bottom panel of the report view shares the chat transcript
    // pipeline, so when the user came from a report the loaded transcript
    // belongs to a table-chat session and isn't in chat.conversations.
    // Always reset on tab-switch: jump to the most recently touched chat
    // (pin status ignored — pinned items live at the top of the sidebar
    // list but the user wants their actual most-recent conversation here),
    // or fall back to the empty new-chat homepage when there's no history.
    const mostRecent = mostRecentConversation(chat.conversations)
    if (mostRecent) {
      chat.loadConversation(mostRecent.id)
    } else {
      chat.newConversation()
    }
  }

  const switchToTables = () => {
    if (!activeTableId && tables.tables.length > 0) {
      openTable(tables.tables[0].id)
    } else if (!activeTableId) {
      // No reports yet — clear any chat and show the empty new-report
      // screen so the user can start one.
      chat.newConversation()
      setPendingReport(true)
    }
  }

  const newTableChat = () => {
    // Don't create a session up front — show the empty new-report screen
    // and defer creation until the user sends the first message. Mirrors
    // the regular "New chat" flow.
    setActiveTableId(null)
    chat.newConversation()
    setPendingReport(true)
  }

  // First-send handler from the empty-report screen: creates the table_chat
  // session, then sends the message into it (using overrideConversationId
  // so the optimistic turn lands in the new session before SSE confirms).
  const sendFirstReportMessage = async (
    message: string,
    opts: {
      provider: string
      model: string
      toolChoice: 'auto' | 'required' | 'none'
      tableMode?: 'explore' | 'edit_table'
      tableSize?: number | 'auto'
    },
  ) => {
    let created
    try {
      created = await tables.create({
        provider: opts.provider,
        model: opts.model,
      })
    } catch {
      return
    }
    setActiveTableId(created.id)
    setPendingReport(false)
    // Fire-and-forget; refresh the tables list when the stream finishes so
    // the sidebar picks up the title that the SQL projection derives from
    // the first user turn (otherwise the row stays as "New conversation").
    chat
      .send(message, {
        provider: opts.provider,
        model: opts.model,
        toolChoice: opts.toolChoice,
        tableMode: opts.tableMode,
        tableMaxRows: typeof opts.tableSize === 'number' ? opts.tableSize : undefined,
        overrideConversationId: created.id,
        onReportCreated: handleReportCreated,
      })
      .finally(() => {
        tables.refresh()
      })
  }

  // Wired into every send call below — when the agent calls `create_report`,
  // refresh the sidebar list so the new entry appears, then auto-navigate.
  const handleReportCreated = ({ report_id }: { report_id: string }) => {
    tables.refresh()
    openTable(report_id)
  }

  return (
    <>
      <AppShell
        inspectorAvailable={
          !activeTableId &&
          !pendingReport &&
          !!chat.transcript &&
          chat.transcript.turns.length > 0
        }
        sidebar={
          <Sidebar
            user={user}
            onLogout={onLogout}
            onOpenSettings={() => setSettingsOpen(true)}
            onNewChat={() => {
              setActiveTableId(null)
              setPendingReport(false)
              chat.newConversation()
            }}
            onOpenConversation={openConversation}
            onSwitchToChats={switchToChats}
            activeTableId={activeTableId}
            pendingReport={pendingReport}
            onOpenTable={openTable}
            onSwitchToTables={switchToTables}
            onNewTable={newTableChat}
          />
        }
        inspector={<Inspector />}
        main={
          activeTableId ? (
            <TableChatView
              key={activeTableId}
              activeTableId={activeTableId}
              onClose={() => {
                // Fired after the user deletes the report. Drop the now-stale
                // conversation transcript and land on the empty new-report
                // screen rather than the chat view.
                setActiveTableId(null)
                chat.newConversation()
                setPendingReport(true)
              }}
              onReportCreated={handleReportCreated}
            />
          ) : pendingReport ? (
            <EmptyReportScreen
              streaming={chat.streamStatus === 'streaming'}
              onSend={sendFirstReportMessage}
              onStop={chat.stop}
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
                onSend={(message, opts) =>
                  chat.send(message, { ...opts, onReportCreated: handleReportCreated })
                }
                onStop={chat.stop}
              />
            </>
          ) : (
            <EmptyThread>
              <Composer
                variant="centered"
                streaming={chat.streamStatus === 'streaming'}
                onSend={(message, opts) =>
                  chat.send(message, { ...opts, onReportCreated: handleReportCreated })
                }
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
          setActiveTableId(null)
          setPendingReport(false)
          chat.newConversation()
        }}
        onPickConversation={openConversation}
        onOpenSettings={() => setSettingsOpen(true)}
        onSignOut={onLogout}
      />
    </>
  )
}
