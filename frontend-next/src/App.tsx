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
import { DatabaseView } from '@/components/database/DatabaseView'
import { useAuth, type AuthUser } from '@/lib/auth'
import { ChatProvider, useChatContext } from '@/lib/chatContext'
import { TablesProvider, useTablesContext } from '@/lib/tablesContext'
import { useActiveTable } from '@/lib/activeTable'
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
  // The Database browser is a separate top-level view — no sessions, no
  // transcripts. `selectedDatabaseTable` is the table name the user picked
  // from the sidebar, which the view turns into `SELECT * FROM <t> LIMIT 100`
  // on first paint. `null` means the user opened the tab but hasn't picked.
  const [databaseOpen, setDatabaseOpen] = useState(false)
  const [selectedDatabaseTable, setSelectedDatabaseTable] = useState<string | null>(null)
  const didRestoreRef = useRef(false)

  // Lifted from TableChatView so `handleReportCreated` can call refetch the
  // same way the edit flow's `onTableUpdated` calls refetch — i.e. the SSE
  // event is the trigger, not the (timing-fragile) mount-effect inside the
  // freshly-mounted TableChatView.
  const activeTable = useActiveTable(activeTableId)

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
    setDatabaseOpen(false)
    chat.loadConversation(id)
  }

  const openTable = (id: string) => {
    setActiveTableId(id)
    setPendingReport(false)
    setDatabaseOpen(false)
    // The table-chat view reuses the chat transcript pipeline for the bottom
    // chat panel — load the same conversation so `chat.send` posts there.
    chat.loadConversation(id)
  }

  const openDatabase = (tableName?: string) => {
    setActiveTableId(null)
    setPendingReport(false)
    setDatabaseOpen(true)
    if (tableName) setSelectedDatabaseTable(tableName)
  }

  const switchToChats = () => {
    setActiveTableId(null)
    setPendingReport(false)
    setDatabaseOpen(false)
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
    setDatabaseOpen(false)
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
    setDatabaseOpen(false)
    chat.newConversation()
    setPendingReport(true)
  }

  // Called from the Database view's "Save as Report" success path — the
  // route returns a fresh table_chat session id, navigate to it.
  const handleDatabaseSaveAsReport = (conversationId: string) => {
    tables.refresh()
    openTable(conversationId)
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
        overrideConversationId: created.id,
        onReportCreated: handleReportCreated,
        // The first send into a fresh report can trigger set_table —
        // mirror TableChatView's edit-flow refetch so the table populates
        // without waiting on the (timing-fragile) mount-effect alone.
        onTableUpdated: () => refetchActiveTableRef.current(),
      })
      .finally(() => {
        tables.refresh()
      })
  }

  // Always-fresh ref to `activeTable.refetch` so the create-report handler
  // can fire it after the navigation re-render, when refetch's closure is
  // bound to the new activeTableId.
  const refetchActiveTableRef = useRef(activeTable.refetch)
  useEffect(() => {
    refetchActiveTableRef.current = activeTable.refetch
  }, [activeTable.refetch])

  // Wired into every send call below — when the agent calls `create_report`,
  // refresh the sidebar list, navigate into the new report, then refetch the
  // table state. The trailing refetch mirrors how the edit flow's
  // `onTableUpdated` calls refetch on every `table_updated` SSE event:
  // making the SSE event the trigger (rather than relying on the freshly
  // mounted TableChatView's mount-effect) is the reliable path.
  const handleReportCreated = ({ report_id }: { report_id: string }) => {
    tables.refresh()
    openTable(report_id)
    // setTimeout(0) defers past React's commit so the ref is rebound to the
    // refetch closure for the new activeTableId before we call it.
    setTimeout(() => refetchActiveTableRef.current(), 0)
  }

  return (
    <>
      <AppShell
        inspectorAvailable={
          !activeTableId &&
          !pendingReport &&
          !databaseOpen &&
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
              setDatabaseOpen(false)
              chat.newConversation()
            }}
            onOpenConversation={openConversation}
            onSwitchToChats={switchToChats}
            activeTableId={activeTableId}
            pendingReport={pendingReport}
            onOpenTable={openTable}
            onSwitchToTables={switchToTables}
            onNewTable={newTableChat}
            databaseOpen={databaseOpen}
            onOpenDatabase={openDatabase}
            onSwitchToDatabase={() => openDatabase()}
          />
        }
        inspector={<Inspector />}
        main={
          databaseOpen ? (
            <DatabaseView
              selectedTable={selectedDatabaseTable}
              onSelectedTableChange={setSelectedDatabaseTable}
              onSaveAsReport={handleDatabaseSaveAsReport}
            />
          ) : activeTableId ? (
            <TableChatView
              key={activeTableId}
              activeTableId={activeTableId}
              table={activeTable.table}
              tableLoading={activeTable.loading}
              tableError={activeTable.error}
              refetchTable={activeTable.refetch}
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
          setDatabaseOpen(false)
          chat.newConversation()
        }}
        onPickConversation={openConversation}
        onOpenSettings={() => setSettingsOpen(true)}
        onSignOut={onLogout}
      />
    </>
  )
}
