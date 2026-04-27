import { useMemo, useState } from 'react'
import { PanelLeftClose, Plus, Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import logoUrl from '@/assets/logo.png'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { ConversationRow } from '@/components/sidebar/ConversationRow'
import { UserWidget } from '@/components/sidebar/UserWidget'
import { ExportRow } from '@/components/exports/ExportRow'
import { useLayout } from '@/components/layout/AppShell'
import { useChatContext } from '@/lib/chatContext'
import { useExportsContext } from '@/lib/exportsContext'
import { ageDays } from '@/lib/datetime'
import type { ConversationInfo } from '@/lib/types'
import type { ExportInfo } from '@/lib/exports'
import type { AuthUser } from '@/lib/auth'

interface SidebarProps {
  user: AuthUser
  onLogout: () => void
  onOpenSettings?: () => void
  onNewChat?: () => void
  onOpenConversation?: (id: string) => void
  activeExportId?: string | null
  onOpenExport?: (id: string) => void
  /** Called when the user switches to the Chats tab while a report is open. */
  onSwitchToChats?: () => void
}

export function Sidebar({
  user,
  onLogout,
  onOpenSettings,
  onNewChat,
  onOpenConversation,
  activeExportId,
  onOpenExport,
  onSwitchToChats,
}: SidebarProps) {
  const chat = useChatContext()
  const exportsStore = useExportsContext()
  const layout = useLayout()
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    if (!query.trim()) return chat.conversations
    const q = query.trim().toLowerCase()
    return chat.conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [chat.conversations, query])

  const filteredExports = useMemo(() => {
    if (!query.trim()) return exportsStore.exports
    const q = query.trim().toLowerCase()
    return exportsStore.exports.filter((e) => e.title.toLowerCase().includes(q))
  }, [exportsStore.exports, query])

  const groupedChats = useMemo(() => groupConversations(filtered), [filtered])
  const groupedExports = useMemo(() => groupExports(filteredExports), [filteredExports])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center px-3 py-3">
        <img
          src={logoUrl}
          alt=""
          aria-hidden="true"
          className="size-12 shrink-0 object-contain"
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

      <div className="px-2 pt-1">
        <Button
          variant="ghost"
          className="h-10 w-full justify-start gap-2.5 text-base font-medium text-sidebar-foreground hover:bg-sidebar-accent"
          onClick={onNewChat}
        >
          <Plus className="size-5" />
          New chat
        </Button>
      </div>

      <Tabs
        value={activeExportId ? 'reports' : 'chats'}
        onValueChange={(next) => {
          if (next === 'reports' && !activeExportId) {
            const mostRecent = exportsStore.exports[0]
            if (mostRecent && onOpenExport) onOpenExport(mostRecent.id)
          } else if (next === 'chats' && activeExportId) {
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
              />
            ))
          )}
        </TabsContent>
        <TabsContent
          value="reports"
          className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2"
        >
          {exportsStore.status === 'loading' ? (
            <SidebarMessage label="Loading…" />
          ) : exportsStore.status === 'error' ? (
            <SidebarMessage label="Couldn't load reports" />
          ) : filteredExports.length === 0 ? (
            <SidebarMessage label={query ? 'No matches' : 'No CSV exports yet'} />
          ) : (
            groupedExports.map((g) => (
              <ExportGroup
                key={g.label}
                label={g.label}
                items={g.items}
                activeId={activeExportId ?? null}
                onPick={onOpenExport}
                onDelete={exportsStore.remove}
                onRename={exportsStore.rename}
                onSetPinned={exportsStore.setPinned}
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
}: {
  label?: string
  items: ReturnType<typeof useChatContext>['conversations']
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
          <ConversationRow
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

function ExportGroup({
  label,
  items,
  activeId,
  onPick,
  onDelete,
  onRename,
  onSetPinned,
}: {
  label?: string
  items: ExportInfo[]
  activeId: string | null
  onPick?: (id: string) => void
  onDelete: (id: string) => Promise<void>
  onRename: (id: string, title: string) => Promise<void>
  onSetPinned: (id: string, pinned: boolean) => Promise<void>
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
          <ExportRow
            key={item.id}
            item={item}
            active={activeId === item.id}
            onPick={onPick}
            onDelete={onDelete}
            onRename={onRename}
            onSetPinned={onSetPinned}
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

function groupExports(items: ExportInfo[]): { label: string; items: ExportInfo[] }[] {
  const pinned: ExportInfo[] = []
  const today: ExportInfo[] = []
  const week: ExportInfo[] = []
  const month: ExportInfo[] = []
  const older: ExportInfo[] = []

  for (const e of items) {
    if (e.pinned_at) {
      pinned.push(e)
      continue
    }
    const age = ageDays(e.created_at)
    if (age < 1) today.push(e)
    else if (age < 7) week.push(e)
    else if (age < 30) month.push(e)
    else older.push(e)
  }

  return [
    { label: 'Pinned', items: pinned },
    { label: 'Today', items: today },
    { label: 'This week', items: week },
    { label: 'This month', items: month },
    { label: 'Older', items: older },
  ].filter((g) => g.items.length > 0)
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
