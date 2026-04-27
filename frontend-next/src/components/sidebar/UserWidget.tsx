import { useState } from 'react'
import { ChevronUp, Droplet, LogOut, Monitor, Moon, Settings, Sun } from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useTheme, type ThemeMode } from '@/lib/theme'
import type { AuthUser } from '@/lib/auth'

interface UserWidgetProps {
  user: AuthUser
  onLogout: () => void
  onOpenSettings?: () => void
}

function initial(email: string): string {
  return email.trim().slice(0, 1).toUpperCase() || '?'
}

export function UserWidget({ user, onLogout, onOpenSettings }: UserWidgetProps) {
  const theme = useTheme()
  const [confirmSignOut, setConfirmSignOut] = useState(false)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none"
          >
            <div className="grid size-8 place-items-center rounded-full bg-sidebar-accent font-medium text-sm">
              {initial(user.email)}
            </div>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-medium">{user.email}</span>
              <span className="text-2xs capitalize text-muted-foreground">{user.role}</span>
            </div>
            <ChevronUp className="ml-auto size-4 shrink-0 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" className="w-56">
          <DropdownMenuLabel className="text-2xs uppercase tracking-[0.14em] text-muted-foreground">
            {user.email}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={onOpenSettings}>
            <Settings className="size-4" />
            Settings
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-2xs uppercase tracking-[0.14em] text-muted-foreground">
            Theme
          </DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={theme.mode}
            onValueChange={(v) => theme.setMode(v as ThemeMode)}
          >
            <DropdownMenuRadioItem value="light">
              <Sun className="size-4" />
              Light
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">
              <Moon className="size-4" />
              Dark
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="cobalt">
              <Droplet className="size-4" />
              Cobalt
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="system">
              <Monitor className="size-4" />
              System
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirmSignOut(true)}>
            <LogOut className="size-4" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={confirmSignOut} onOpenChange={setConfirmSignOut}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign out of this account?</AlertDialogTitle>
            <AlertDialogDescription>
              You'll be returned to the sign-in screen. Your conversations stay where they are.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={onLogout}
            >
              Sign out
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
