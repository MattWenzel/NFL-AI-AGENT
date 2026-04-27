import { useMemo, useState } from 'react'
import { PanelLeftClose, Plus, Search, Table2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import logoUrl from '@/assets/logo.png'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { ConversationRow } from '@/components/sidebar/ConversationRow'
import { TableRow } from '@/components/sidebar/TableRow'
import { UserWidget } from '@/components/sidebar/UserWidget'
import { useLayout } from '@/components/layout/AppShell'
import { useChatContext } from '@/lib/chatContext'
import { useTablesContext } from '@/lib/tablesContext'
import type { ConversationInfo } from '@/lib/types'
import type { AuthUser } from '@/lib/auth'
import { ageDays } from '@/lib/datetime'

interface SidebarProps {
  user: AuthUser
  onLogout: () => void
  onOpenSettings?: () => void
  onNewChat?: () => void
  onOpenConversation?: (id: string) => void
  /** Called when the user switches to the Chats tab while a report is open. */
  onSwitchToChats?: () => void
  /** Active report id (when the report view is open). Internally still
   *  backed by the table-chat session — the rename is UI-only. */
  activeTableId?: string | null
  /** True while the user is on the new-report empty screen (no session
   *  exists yet). Highlights the Reports tab without highlighting any row. */
  pendingReport?: boolean
  onOpenTable?: (id: string) => void
  onSwitchToTables?: () => void
  onNewTable?: () => void
}

export function Sidebar({
  user,
  onLogout,
  onOpenSettings,
  onNewChat,
  onOpenConversation,
  onSwitchToChats,
  activeTableId,
  pendingReport = false,
  onOpenTable,
  onSwitchToTables,
  onNewTable,
}: SidebarProps) {
  const chat = useChatContext()
  const tablesStore = useTablesContext()
  const layout = useLayout()
  const [query, setQuery] = useState('')

  const activeTab: 'chats' | 'reports' = activeTableId || pendingReport ? 'reports' : 'chats'

  const filtered = useMemo(() => {
    if (!query.trim()) return chat.conversations
    const q = query.trim().toLowerCase()
    return chat.conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [chat.conversations, query])

  const filteredTables = useMemo(() => {
    if (!query.trim()) return tablesStore.tables
    const q = query.trim().toLowerCase()
    return tablesStore.tables.filter((t) => t.title.toLowerCase().includes(q))
  }, [tablesStore.tables, query])

  const groupedChats = useMemo(() => groupConversations(filtered), [filtered])
  const groupedTables = useMemo(() => groupConversations(filteredTables), [filteredTables])

  // Map each parent chat → its most-recently-touched linked report so the
  // ConversationRow can surface a "this chat created a report" affordance.
  const reportsByChat = useMemo(() => {
    const map = new Map<string, ConversationInfo>()
    for (const t of tablesStore.tables) {
      const parent = t.source_session_id
      if (!parent) continue
      const existing = map.get(parent)
      if (!existing || (existing.updated_at ?? '') < (t.updated_at ?? '')) {
        map.set(parent, t)
      }
    }
    return map
  }, [tablesStore.tables])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center px-3 py-3">
        <img
          src={logoUrl}
          alt=""
          aria-hidden="true"
          className="size-20 shrink-0 object-contain"
        />
        <span className="ml-2.5 font-display text-lg font-semibold tracking-tight">NFL Stats</span>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto hidden size-8 md:flex"
          onClick={layout.toggleDesktopSidebar}
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose className="size-4" />
        </Button>
      </div>

      <div className="space-y-1 px-2 pt-1">
        <Button
          variant="ghost"
          className="h-10 w-full justify-start gap-2.5 text-base font-medium text-sidebar-foreground hover:bg-sidebar-accent"
          onClick={onNewChat}
        >
          <Plus className="size-5" />
          New chat
        </Button>
        {onNewTable ? (
          <Button
            variant="ghost"
            className="h-10 w-full justify-start gap-2.5 text-base font-medium text-sidebar-foreground hover:bg-sidebar-accent"
            onClick={onNewTable}
          >
            <Table2 className="size-5" />
            New report
          </Button>
        ) : null}
      </div>

      <Tabs
        value={activeTab}
        onValueChange={(next) => {
          if (next === 'reports' && activeTab !== 'reports') {
            onSwitchToTables?.()
          } else if (next === 'chats' && activeTab !== 'chats') {
            onSwitchToChats?.()
          }
        }}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList className="mx-3 mt-5 grid h-11 grid-cols-2 gap-1 bg-sidebar-accent/60 p-1">
          <TabsTrigger value="chats" className="text-base font-medium">
            Chats
          </TabsTrigger>
          <TabsTrigger value="reports" className="text-base font-medium">
            Reports
          </TabsTrigger>
        </TabsList>

        <div className="px-3 pb-2 pt-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              placeholder="Search conversations"
              className="h-8 pl-8 text-sm"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>

        <TabsContent
          value="chats"
          className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2"
        >
          {chat.conversationsStatus === 'loading' ? (
            <SidebarMessage label="Loading…" />
          ) : chat.conversationsStatus === 'error' ? (
            <SidebarMessage label="Couldn't load conversations" />
          ) : filtered.length === 0 ? (
            <SidebarMessage label={query ? 'No matches' : 'No conversations yet'} />
          ) : (
            groupedChats.map((g) => (
              <SidebarGroup
                key={g.label}
                label={g.label}
                items={g.items}
                activeId={chat.conversationId}
                onPick={onOpenConversation}
                reportsByChat={reportsByChat}
                onOpenReport={onOpenTable}
              />
            ))
          )}
        </TabsContent>
        <TabsContent
          value="reports"
          className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2"
        >
          {tablesStore.status === 'loading' ? (
            <SidebarMessage label="Loading…" />
          ) : tablesStore.status === 'error' ? (
            <SidebarMessage label="Couldn't load reports" />
          ) : filteredTables.length === 0 ? (
            <SidebarMessage label={query ? 'No matches' : 'No reports yet'} />
          ) : (
            groupedTables.map((g) => (
              <TableGroup
                key={g.label}
                label={g.label}
                items={g.items}
                activeId={activeTableId ?? null}
                onPick={onOpenTable}
              />
            ))
          )}
        </TabsContent>
      </Tabs>

      <div className="shrink-0 border-t border-sidebar-border p-2">
        <UserWidget user={user} onLogout={onLogout} onOpenSettings={onOpenSettings} />
      </div>
    </div>
  )
}

function SidebarGroup({
  label,
  items,
  activeId,
  onPick,
  reportsByChat,
  onOpenReport,
}: {
  label?: string
  items: ReturnType<typeof useChatContext>['conversations']
  activeId: string | null
  onPick?: (id: string) => void
  reportsByChat: Map<string, ConversationInfo>
  onOpenReport?: (id: string) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="mb-3">
      {label ? (
        <p className="px-2 pt-2 pb-1 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </p>
      ) : null}
      <ul className="space-y-px">
        {items.map((item) => (
          <ConversationRow
            key={item.id}
            item={item}
            active={activeId === item.id}
            onPick={onPick}
            linkedReport={reportsByChat.get(item.id) ?? null}
            onOpenReport={onOpenReport}
          />
        ))}
      </ul>
    </div>
  )
}

function TableGroup({
  label,
  items,
  activeId,
  onPick,
}: {
  label?: string
  items: ConversationInfo[]
  activeId: string | null
  onPick?: (id: string) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="mb-3">
      {label ? (
        <p className="px-2 pt-2 pb-1 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </p>
      ) : null}
      <ul className="space-y-px">
        {items.map((item) => (
          <TableRow
            key={item.id}
            item={item}
            active={activeId === item.id}
            onPick={onPick}
          />
        ))}
      </ul>
    </div>
  )
}

function SidebarMessage({ label }: { label: string }) {
  return (
    <div className="grid flex-1 place-items-center px-4 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  )
}

function groupConversations(items: ConversationInfo[]): { label: string; items: ConversationInfo[] }[] {
  const pinned: ConversationInfo[] = []
  const today: ConversationInfo[] = []
  const week: ConversationInfo[] = []
  const month: ConversationInfo[] = []
  const older: ConversationInfo[] = []

  for (const c of items) {
    if (c.pinned_at) {
      pinned.push(c)
      continue
    }
    const age = ageDays(c.updated_at)
    if (age < 1) today.push(c)
    else if (age < 7) week.push(c)
    else if (age < 30) month.push(c)
    else older.push(c)
  }

  return [
    { label: 'Pinned', items: pinned },
    { label: 'Today', items: today },
    { label: 'This week', items: week },
    { label: 'This month', items: month },
    { label: 'Older', items: older },
  ].filter((g) => g.items.length > 0)
}
