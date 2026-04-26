import { useState } from 'react'
import { MoreHorizontal, Pin, PinOff, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useChatContext } from '@/lib/chatContext'
import { relativeTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { ConversationInfo } from '@/lib/types'

interface ConversationRowProps {
  item: ConversationInfo
  active: boolean
  onPick?: (id: string) => void
}

export function ConversationRow({ item, active, onPick }: ConversationRowProps) {
  const chat = useChatContext()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const isPinned = !!item.pinned_at

  const togglePin = async () => {
    try {
      await chat.setPinned(item.id, !isPinned)
      toast.success(isPinned ? 'Unpinned' : 'Pinned')
    } catch {
      toast.error('Could not update')
    }
  }

  const remove = async () => {
    try {
      await chat.removeConversation(item.id)
      toast.success('Conversation deleted')
    } catch {
      toast.error('Could not delete')
    }
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={() => onPick?.(item.id)}
        className={cn(
          'flex w-full flex-col gap-0.5 rounded-md px-2 py-2 pr-9 text-left transition-colors',
          'hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none',
          active && 'bg-sidebar-accent',
        )}
      >
        <span className="line-clamp-1 text-sm font-medium text-sidebar-foreground">
          {item.title}
        </span>
        <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
          {item.provider ? <span className="capitalize">{item.provider}</span> : null}
          {item.provider && item.updated_at ? <span aria-hidden>·</span> : null}
          {item.updated_at ? <span className="tabular">{relativeTime(item.updated_at)}</span> : null}
          {isPinned ? (
            <>
              <span aria-hidden>·</span>
              <Pin className="size-3 text-accent" aria-label="Pinned" />
            </>
          ) : null}
        </span>
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              'absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-md text-muted-foreground transition-opacity',
              'hover:bg-sidebar-accent hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none',
              'opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100',
            )}
            aria-label="Conversation actions"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem onSelect={togglePin}>
            {isPinned ? <PinOff className="size-4" /> : <Pin className="size-4" />}
            {isPinned ? 'Unpin' : 'Pin'}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirmDelete(true)}>
            <Trash2 className="size-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              "{item.title}" will be removed permanently, including all turns and tool runs.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={remove}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}
