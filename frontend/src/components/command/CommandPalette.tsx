import { useEffect } from 'react'
import {
  Droplet,
  MessageSquarePlus,
  Moon,
  MessageCircle,
  Settings as SettingsIcon,
  Sun,
  SunMoon,
  Zap,
} from 'lucide-react'

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import { useChatContext } from '@/lib/state/chatContext'
import { useTheme } from '@/lib/theme'

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNewChat: () => void
  onPickConversation: (id: string) => void
  onOpenSettings: () => void
  onSignOut: () => void
}

export function CommandPalette({
  open,
  onOpenChange,
  onNewChat,
  onPickConversation,
  onOpenSettings,
  onSignOut,
}: CommandPaletteProps) {
  const chat = useChatContext()
  const theme = useTheme()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isMac = typeof navigator !== 'undefined' && /Mac|iPad|iPhone/.test(navigator.platform)
      const meta = isMac ? e.metaKey : e.ctrlKey
      if (meta && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        onOpenChange(!open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onOpenChange])

  const close = (action: () => void) => {
    action()
    onOpenChange(false)
  }

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Command palette">
      <CommandInput placeholder="Search conversations or pick an action…" />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>
        <CommandGroup heading="Actions">
          <CommandItem onSelect={() => close(onNewChat)}>
            <MessageSquarePlus className="size-4" />
            New chat
          </CommandItem>
          <CommandItem onSelect={() => close(onOpenSettings)}>
            <SettingsIcon className="size-4" />
            Open settings
          </CommandItem>
          <CommandItem onSelect={() => close(onSignOut)}>Sign out</CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Theme">
          <CommandItem onSelect={() => close(() => theme.setMode('light'))}>
            <Sun className="size-4" />
            Light
          </CommandItem>
          <CommandItem onSelect={() => close(() => theme.setMode('dark'))}>
            <Moon className="size-4" />
            Dark
          </CommandItem>
          <CommandItem onSelect={() => close(() => theme.setMode('cobalt'))}>
            <Droplet className="size-4" />
            Cobalt
          </CommandItem>
          <CommandItem onSelect={() => close(() => theme.setMode('cyberpunk'))}>
            <Zap className="size-4" />
            Cyberpunk
          </CommandItem>
          <CommandItem onSelect={() => close(() => theme.setMode('system'))}>
            <SunMoon className="size-4" />
            System
          </CommandItem>
        </CommandGroup>
        {chat.conversations.length > 0 ? (
          <>
            <CommandSeparator />
            <CommandGroup heading="Conversations">
              {chat.conversations.slice(0, 30).map((c) => (
                <CommandItem
                  key={c.id}
                  value={`${c.title} ${c.id}`}
                  onSelect={() => close(() => onPickConversation(c.id))}
                >
                  <MessageCircle className="size-4" />
                  <span className="truncate">{c.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : null}
      </CommandList>
    </CommandDialog>
  )
}
