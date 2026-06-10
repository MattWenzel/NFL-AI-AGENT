import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { AppShell } from '@/components/layout/AppShell'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { Composer } from '@/components/composer/Composer'
import { Thread } from '@/components/thread/Thread'
import { AuroraBackground } from '@/components/thread/AuroraBackground'
import { EmptyThread } from '@/components/thread/EmptyThread'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { Toaster } from '@/components/ui/sonner'
import { AuthWall } from '@/components/auth/AuthWall'
import { CommandPalette } from '@/components/command/CommandPalette'
import { SettingsModal } from '@/components/settings/SettingsModal'
import { TableChatView } from '@/components/tables/TableChatView'
import { EmptyReportScreen } from '@/components/tables/EmptyReportScreen'
import { DatabaseView, type DatabaseViewHandle } from '@/components/database/DatabaseView'
import { useAuth, type AuthUser } from '@/lib/state/auth'
import { ChatProvider, useChatContext } from '@/lib/state/chatContext'
import { NavigationProvider } from '@/lib/state/navigation'
import { useSurface } from '@/lib/state/surface'
import { TablesProvider, useTablesContext } from '@/lib/state/tablesContext'
import { useActiveTable } from '@/lib/state/activeTable'
import { useDbHelperChat } from '@/lib/state/dbHelperChat'
import type { ConversationInfo } from '@/lib/types'

export default function App() {
  const auth = useAuth()

  // Reset the URL to `/` when the user signs out so a subsequent
  // sign-in (same or different account) lands on the empty new-chat
  // screen instead of inheriting the previous user's deep link — that
  // chat ID won't be visible to the new account and the load would
  // fail with "Failed to load conversation". Only fires on the
  // authenticated→anonymous transition so initial-load deep-links
  // from a signed-out session are preserved.
  const prevAuthStatusRef = useRef(auth.state.status)
  useEffect(() => {
    const prev = prevAuthStatusRef.current
    prevAuthStatusRef.current = auth.state.status
    if (
      prev === 'authenticated' &&
      auth.state.status === 'anonymous' &&
      typeof window !== 'undefined' &&
      window.location.pathname !== '/'
    ) {
      window.history.replaceState(null, '', '/')
    }
  }, [auth.state.status])

  // A password-reset link (`/#/reset?token=…`) clicked while already
  // signed in never reaches the AuthWall, which owns the reset form.
  // Don't leave it a silent no-op: point at Settings and scrub the hash
  // (token included) from the URL and history.
  useEffect(() => {
    if (auth.state.status !== 'authenticated') return
    if (!window.location.hash.startsWith('#/reset')) return
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    toast.info("You're already signed in — change your password from Settings → Account.")
  }, [auth.state.status])

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
  const { surface, navigate } = useSurface()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Project the URL-driven Surface into the per-area state the rest of
  // the workspace expects. Refreshing or arriving via deep-link
  // hydrates these from the URL automatically.
  const activeTableId = surface.kind === 'report' ? surface.id : null
  const pendingReport = surface.kind === 'pending-report'
  const databaseOpen = surface.kind === 'database'
  const selectedDatabaseTable = surface.kind === 'database' ? surface.table ?? null : null

  // True when the URL points at a chat/report but the chat store hasn't
  // loaded it yet — used to render a blank pane instead of flashing the
  // EmptyThread headline before the transcript arrives.
  const expectedConversationId =
    surface.kind === 'chat' || surface.kind === 'report' ? surface.id : null
  const loadingConversation =
    expectedConversationId !== null && chat.conversationId !== expectedConversationId

  const activeTable = useActiveTable(activeTableId)

  const databaseViewRef = useRef<DatabaseViewHandle | null>(null)

  // Owned at this level so closing/reopening the SQL helper panel inside
  // the Database view doesn't reset the conversation. State is wiped on
  // page refresh — that's the ephemerality we promise.
  const dbHelperChat = useDbHelperChat({
    onRunInEditor: (sql) => {
      databaseViewRef.current?.runQuery(sql)
    },
  })

  // Sync the chat store with the URL. When surface points at a chat or
  // report, ensure that conversation is loaded; on home / pending-report,
  // drop any in-flight chat so the previous conversation doesn't bleed
  // into the next view (Inspector / streaming state). On database, just
  // clear the inspector selection — there's no transcript to preserve.
  useEffect(() => {
    if (surface.kind === 'chat' || surface.kind === 'report') {
      if (chat.conversationId !== surface.id) {
        chat.loadConversation(surface.id)
      }
    } else if (surface.kind === 'home' || surface.kind === 'pending-report') {
      if (chat.conversationId !== null || chat.transcript !== null) {
        chat.newConversation()
      }
    } else if (surface.kind === 'database') {
      chat.clearSelection()
    }
    // chat methods are stable (useCallback'd); the surface is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [surface])

  // When a fresh chat picks up its real conversation_id (from the SSE
  // 'conversation_id' event after the first send), promote the URL from
  // `/` to `/chat/:id` so the back button can return here later. Replace
  // (not push) — there's no meaningful prior history entry to preserve.
  //
  // Gate on `streamStatus !== 'idle'` so we only fire during the
  // send-from-home window. Without this guard, clicking "New chat" while
  // on /chat/abc would re-promote to /chat/abc: both this effect and
  // the surface-sync effect run on the same render, but surface-sync's
  // dispatch hasn't reduced into chat.conversationId yet — so we'd see
  // the stale 'abc' and bounce the URL back.
  useEffect(() => {
    if (
      surface.kind === 'home' &&
      chat.conversationId &&
      chat.streamStatus !== 'idle'
    ) {
      navigate({ kind: 'chat', id: chat.conversationId }, { replace: true })
    }
  }, [surface.kind, chat.conversationId, chat.streamStatus, navigate])

  // If the active report disappears from the tables list (deleted from
  // the sidebar's 3-dot menu, the toolbar Delete, or another tab), bail
  // back to the empty new-report screen. Replace so the dead URL doesn't
  // sit in the back stack.
  useEffect(() => {
    if (surface.kind !== 'report') return
    if (tables.status !== 'ready') return
    if (tables.tables.some((t) => t.id === surface.id)) return
    navigate({ kind: 'pending-report' }, { replace: true })
  }, [surface, tables.status, tables.tables, navigate])

  // Same bailout for the chat surface. Skip the bail when the chat is
  // currently loaded (conversationId === surface.id) — that's the
  // post-send window where the conversations list may not have caught
  // up yet but the chat is real.
  useEffect(() => {
    if (surface.kind !== 'chat') return
    if (chat.conversationsStatus !== 'ready') return
    if (chat.conversationId === surface.id) return
    if (chat.conversations.some((c) => c.id === surface.id)) return
    navigate({ kind: 'home' }, { replace: true })
  }, [
    surface,
    chat.conversationsStatus,
    chat.conversations,
    chat.conversationId,
    navigate,
  ])

  const openConversation = (id: string) => navigate({ kind: 'chat', id })
  const openTable = (id: string) => navigate({ kind: 'report', id })
  const openDatabase = (table?: string) => navigate({ kind: 'database', table })
  const newTableChat = () => navigate({ kind: 'pending-report' })
  const newChat = () => navigate({ kind: 'home' })

  const switchToChats = () => {
    if (surface.kind === 'chat' || surface.kind === 'home') return
    const mostRecent = mostRecentConversation(chat.conversations)
    navigate(mostRecent ? { kind: 'chat', id: mostRecent.id } : { kind: 'home' })
  }

  const switchToTables = () => {
    if (surface.kind === 'report' || surface.kind === 'pending-report') return
    if (tables.tables.length > 0) {
      navigate({ kind: 'report', id: tables.tables[0].id })
    } else {
      navigate({ kind: 'pending-report' })
    }
  }

  // Called from the Database view's "Save as Report" success path — the
  // route returns a fresh table_chat session id, navigate to it.
  const handleDatabaseSaveAsReport = (conversationId: string) => {
    tables.refresh()
    openTable(conversationId)
  }

  // First-send handler from the empty-report screen: creates the
  // table_chat session, then sends the message into it. Replace (not
  // push) the URL from /report/new → /report/:id so back returns to
  // wherever the user came from, not to the empty new-report screen.
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
    navigate({ kind: 'report', id: created.id }, { replace: true })
    chat
      .send(message, {
        provider: opts.provider,
        model: opts.model,
        toolChoice: opts.toolChoice,
        overrideConversationId: created.id,
        onReportCreated: handleReportCreated,
        onTableUpdated: () => refetchActiveTableRef.current(),
      })
      .finally(() => {
        tables.refresh()
      })
  }

  // Always-fresh ref to `activeTable.refetch` so send handlers can fire
  // it after the navigation re-render, when refetch's closure is bound
  // to the new activeTableId.
  const refetchActiveTableRef = useRef(activeTable.refetch)
  useEffect(() => {
    refetchActiveTableRef.current = activeTable.refetch
  }, [activeTable.refetch])

  // When the agent calls `create_report`, refresh the sidebar so the new
  // report shows up there. We don't auto-navigate — the AgentResponse
  // renders an inline link card the user clicks to open the report.
  const handleReportCreated = (_payload: { report_id: string }) => {
    tables.refresh()
  }

  const sendReportTurn = (
    message: string,
    opts: {
      provider: string
      model: string
      toolChoice: 'auto' | 'required' | 'none'
    },
  ) => {
    chat
      .send(message, {
        provider: opts.provider,
        model: opts.model,
        toolChoice: opts.toolChoice,
        onTableUpdated: () => refetchActiveTableRef.current(),
        onReportCreated: handleReportCreated,
      })
      .finally(() => {
        tables.refresh()
      })
  }

  return (
    <NavigationProvider openReport={openTable}>
      <AppShell
        onBrandClick={newChat}
        surfaceKey={
          databaseOpen
            ? 'database'
            : activeTableId
              ? `table:${activeTableId}`
              : `chat:${chat.transcript?.session_id ?? 'new'}`
        }
        inspectorAvailable={
          !databaseOpen &&
          !pendingReport &&
          !!chat.transcript &&
          chat.transcript.turns.length > 0
        }
        sidebar={
          <Sidebar
            user={user}
            onLogout={onLogout}
            onOpenSettings={() => setSettingsOpen(true)}
            onNewChat={newChat}
            onOpenConversation={openConversation}
            onSwitchToChats={switchToChats}
            activeTableId={activeTableId}
            pendingReport={pendingReport}
            onOpenTable={openTable}
            onSwitchToTables={switchToTables}
            onNewTable={newTableChat}
            databaseOpen={databaseOpen}
            selectedDatabaseTable={selectedDatabaseTable}
            onOpenDatabase={openDatabase}
            onSwitchToDatabase={() => openDatabase()}
          />
        }
        inspector={<Inspector />}
        main={
          // Per-surface failure containment: a render crash in one surface
          // shows an inline error card instead of white-screening the app.
          // Keyed by surface so navigating away resets the boundary.
          <ErrorBoundary
            key={
              databaseOpen
                ? 'database'
                : activeTableId
                  ? `report:${activeTableId}`
                  : pendingReport
                    ? 'pending-report'
                    : 'chat'
            }
            label={
              databaseOpen
                ? 'The Database view'
                : activeTableId || pendingReport
                  ? 'This report'
                  : 'This chat'
            }
          >
          {databaseOpen ? (
            <DatabaseView
              ref={databaseViewRef}
              selectedTable={selectedDatabaseTable}
              onSaveAsReport={handleDatabaseSaveAsReport}
              helper={dbHelperChat}
            />
          ) : activeTableId ? (
            loadingConversation ? (
              // Blank pane until the report's transcript loads.
              <div className="flex-1" />
            ) : (
              <TableChatView
                key={activeTableId}
                activeTableId={activeTableId}
                table={activeTable.table}
                tableLoading={activeTable.loading}
                tableError={activeTable.error}
                refetchTable={activeTable.refetch}
                onClose={() => {
                  // Fired after the user deletes the report. Drop the
                  // now-stale URL and land on the empty new-report screen.
                  navigate({ kind: 'pending-report' }, { replace: true })
                }}
                onSend={sendReportTurn}
                streaming={chat.streamStatus === 'streaming'}
                onStop={chat.stop}
              />
            )
          ) : pendingReport ? (
            <EmptyReportScreen
              streaming={chat.streamStatus === 'streaming'}
              onSend={sendFirstReportMessage}
              onStop={chat.stop}
              onSqlCreated={handleDatabaseSaveAsReport}
            />
          ) : loadingConversation ? (
            // Blank pane during deep-link / refresh restore so the
            // EmptyThread headline doesn't flash before the transcript
            // arrives.
            <div className="flex-1" />
          ) : chat.transcript && chat.transcript.turns.length > 0 ? (
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
              <AuroraBackground />
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
            </div>
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
          )}
          </ErrorBoundary>
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
        onNewChat={newChat}
        onPickConversation={openConversation}
        onOpenSettings={() => setSettingsOpen(true)}
        onSignOut={onLogout}
      />
    </NavigationProvider>
  )
}
