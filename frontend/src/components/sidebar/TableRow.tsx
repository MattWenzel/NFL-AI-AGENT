import { useState } from 'react'
import { MoreHorizontal, Pencil, Pin, PinOff, Table2, Trash2 } from 'lucide-react'
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useTablesContext } from '@/lib/state/tablesContext'
import { absoluteTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { ConversationInfo } from '@/lib/types'

interface TableRowProps {
  item: ConversationInfo
  active: boolean
  onPick?: (id: string) => void
}

export function TableRow({ item, active, onPick }: TableRowProps) {
  const tables = useTablesContext()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState(item.title)
  const [renaming, setRenaming] = useState(false)
  const isPinned = !!item.pinned_at

  const togglePin = async () => {
    try {
      await tables.setPinned(item.id, !isPinned)
      toast.success(isPinned ? 'Unpinned' : 'Pinned')
    } catch {
      toast.error('Could not update')
    }
  }

  const remove = async () => {
    try {
      await tables.remove(item.id)
      toast.success('Report deleted')
    } catch {
      toast.error('Could not delete')
    }
  }

  const startRename = () => {
    setRenameValue(item.title)
    setRenameOpen(true)
  }

  const submitRename = async () => {
    const next = renameValue.trim()
    if (!next || next === item.title) {
      setRenameOpen(false)
      return
    }
    setRenaming(true)
    try {
      await tables.rename(item.id, next)
      toast.success('Renamed')
      setRenameOpen(false)
    } catch {
      toast.error('Could not rename')
    } finally {
      setRenaming(false)
    }
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={() => onPick?.(item.id)}
        className={cn(
          'flex w-full flex-col gap-1 rounded-md px-2 py-2 pr-9 text-left transition-colors',
          'focus-visible:outline-none',
          active
            ? 'bg-accent/25 ring-1 ring-inset ring-accent/55 hover:bg-accent/30'
            : 'hover:bg-sidebar-accent focus-visible:bg-sidebar-accent',
        )}
      >
        <div className="flex items-start gap-1.5">
          <Table2 className="size-3.5 shrink-0 mt-0.5 text-muted-foreground" aria-hidden />
          <span className="line-clamp-2 flex-1 text-sm font-medium leading-snug text-sidebar-foreground">
            {item.title}
          </span>
          {isPinned ? (
            <Pin className="size-3 shrink-0 mt-0.5 text-accent" aria-label="Pinned" />
          ) : null}
        </div>
        {item.updated_at ? (
          <span className="pl-5 text-2xs text-muted-foreground tabular">
            {absoluteTime(item.updated_at)}
          </span>
        ) : null}
      </button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              'absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-md text-muted-foreground transition-opacity',
              'hover:bg-sidebar-accent hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none',
              'md:opacity-0 md:group-hover:opacity-100 data-[state=open]:opacity-100',
            )}
            aria-label="Report actions"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem onSelect={startRename}>
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
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

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename report</DialogTitle>
            <DialogDescription>Give this report a clearer title.</DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                submitRename()
              }
            }}
            maxLength={200}
            placeholder="Report title"
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameOpen(false)} disabled={renaming}>
              Cancel
            </Button>
            <Button
              onClick={submitRename}
              disabled={renaming || !renameValue.trim() || renameValue.trim() === item.title}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this report?</AlertDialogTitle>
            <AlertDialogDescription>
              "{item.title}" will be removed permanently, including the chat history and the current table.
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
