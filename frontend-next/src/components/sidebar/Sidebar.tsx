import { useMemo, useState } from 'react'
import { Plus, Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { UserWidget } from '@/components/sidebar/UserWidget'
import { useChatContext } from '@/lib/chatContext'
import { relativeTime } from '@/lib/datetime'
import type { AuthUser } from '@/lib/auth'
import { cn } from '@/lib/utils'

interface SidebarProps {
  user: AuthUser
  onLogout: () => void
  onOpenSettings?: () => void
  onNewChat?: () => void
  onOpenConversation?: (id: string) => void
}

export function Sidebar({ user, onLogout, onOpenSettings, onNewChat, onOpenConversation }: SidebarProps) {
  const chat = useChatContext()
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    if (!query.trim()) return chat.conversations
    const q = query.trim().toLowerCase()
    return chat.conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [chat.conversations, query])

  const { pinned, recent } = useMemo(() => {
    const p = filtered.filter((c) => !!c.pinned_at)
    const r = filtered.filter((c) => !c.pinned_at)
    return { pinned: p, recent: r }
  }, [filtered])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-sidebar-border px-4 py-3">
        <div className="grid size-8 place-items-center rounded-md bg-primary font-display text-base font-semibold text-primary-foreground">
          N
        </div>
        <div className="flex flex-col leading-tight">
          <span className="font-display text-sm font-semibold tracking-tight">NFL Stats</span>
          <span className="text-2xs text-muted-foreground">analytical chat</span>
        </div>
      </div>

      <div className="px-3 py-2">
        <Button
          variant="secondary"
          className="w-full justify-start gap-2 font-medium"
          onClick={onNewChat}
        >
          <Plus className="size-4" />
          New chat
        </Button>
      </div>

      <Tabs defaultValue="chats" className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="mx-3 grid h-9 grid-cols-2 bg-sidebar-accent/60">
          <TabsTrigger value="chats" className="text-xs">Chats</TabsTrigger>
          <TabsTrigger value="reports" className="text-xs">Reports</TabsTrigger>
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
            <>
              {pinned.length > 0 ? (
                <SidebarGroup label="Pinned" items={pinned} activeId={chat.conversationId} onPick={onOpenConversation} />
              ) : null}
              <SidebarGroup
                label={pinned.length > 0 ? 'Recent' : undefined}
                items={recent}
                activeId={chat.conversationId}
                onPick={onOpenConversation}
              />
            </>
          )}
        </TabsContent>
        <TabsContent
          value="reports"
          className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2"
        >
          <SidebarMessage label="No CSV exports yet" />
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
          <li key={item.id}>
            <button
              type="button"
              onClick={() => onPick?.(item.id)}
              className={cn(
                'flex w-full flex-col gap-0.5 rounded-md px-2 py-2 text-left transition-colors',
                'hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none',
                activeId === item.id && 'bg-sidebar-accent',
              )}
            >
              <span className="line-clamp-1 text-sm font-medium text-sidebar-foreground">
                {item.title}
              </span>
              <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
                {item.provider ? <span className="capitalize">{item.provider}</span> : null}
                {item.provider && item.updated_at ? <span aria-hidden>·</span> : null}
                {item.updated_at ? <span className="tabular">{relativeTime(item.updated_at)}</span> : null}
              </span>
            </button>
          </li>
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
