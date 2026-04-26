import { Plus, Search } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

export function Sidebar() {
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
        <Button variant="secondary" className="w-full justify-start gap-2 font-medium">
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
            />
          </div>
        </div>

        <TabsContent value="chats" className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2">
          <SidebarEmptyList label="No conversations yet" />
        </TabsContent>
        <TabsContent value="reports" className="m-0 flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2">
          <SidebarEmptyList label="No CSV exports yet" />
        </TabsContent>
      </Tabs>

      <div className="shrink-0 border-t border-sidebar-border p-2">
        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-sidebar-accent"
        >
          <div className="grid size-8 place-items-center rounded-full bg-sidebar-accent font-medium text-sm">
            ?
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-medium">Not signed in</span>
            <span className="text-2xs text-muted-foreground">Account</span>
          </div>
        </button>
      </div>
    </div>
  )
}

function SidebarEmptyList({ label }: { label: string }) {
  return (
    <div className="grid flex-1 place-items-center px-4 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  )
}
