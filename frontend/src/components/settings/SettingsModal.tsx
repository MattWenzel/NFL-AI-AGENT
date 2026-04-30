import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AccountTab } from '@/components/settings/AccountTab'
import { ProvidersTab } from '@/components/settings/ProvidersTab'
import type { AuthUser } from '@/lib/state/auth'

interface SettingsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  user: AuthUser
  onAccountDeleted: () => void
}

export function SettingsModal({ open, onOpenChange, user, onAccountDeleted }: SettingsModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[85vh] max-h-[720px] gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="shrink-0 border-b border-border px-6 py-4">
          <DialogTitle className="font-display text-2xl font-medium tracking-tight">
            Settings
          </DialogTitle>
        </DialogHeader>
        <Tabs defaultValue="account" className="flex min-h-0 flex-1 flex-col gap-0">
          <TabsList className="mx-6 mt-3 grid h-9 w-fit grid-cols-2">
            <TabsTrigger value="account" className="text-xs">Account</TabsTrigger>
            <TabsTrigger value="providers" className="text-xs">Providers</TabsTrigger>
          </TabsList>
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <TabsContent value="account" className="m-0">
              <AccountTab user={user} onAccountDeleted={onAccountDeleted} />
            </TabsContent>
            <TabsContent value="providers" className="m-0">
              <ProvidersTab />
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
