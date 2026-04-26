import { useState } from 'react'
import { MoreHorizontal, Pencil, Pin, PinOff, Trash2 } from 'lucide-react'
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
import { relativeTime } from '@/lib/datetime'
import { formatBytes, type ExportInfo } from '@/lib/exports'
import { cn } from '@/lib/utils'

interface ExportRowProps {
  item: ExportInfo
  active: boolean
  onPick?: (id: string) => void
  onDelete: (id: string) => Promise<void>
  onRename: (id: string, title: string) => Promise<void>
  onSetPinned: (id: string, pinned: boolean) => Promise<void>
}

export function ExportRow({
  item,
  active,
  onPick,
  onDelete,
  onRename,
  onSetPinned,
}: ExportRowProps) {
  const [confirm, setConfirm] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState(item.title)
  const [renaming, setRenaming] = useState(false)
  const isPinned = !!item.pinned_at

  const remove = async () => {
    try {
      await onDelete(item.id)
      toast.success('CSV deleted')
    } catch {
      toast.error('Could not delete')
    }
  }

  const togglePin = async () => {
    try {
      await onSetPinned(item.id, !isPinned)
      toast.success(isPinned ? 'Unpinned' : 'Pinned')
    } catch {
      toast.error('Could not update')
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
      await onRename(item.id, next)
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
          'flex w-full flex-col gap-0.5 rounded-md px-2 py-2 pr-9 text-left transition-colors',
          'hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:outline-none',
          active && 'bg-sidebar-accent',
        )}
      >
        <div className="flex items-start gap-1.5">
          <span className="line-clamp-1 flex-1 text-sm font-medium text-sidebar-foreground">
            {item.title}
          </span>
          {isPinned ? (
            <Pin className="size-3 shrink-0 mt-0.5 text-accent" aria-label="Pinned" />
          ) : null}
        </div>
        <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
          <span className="tabular">{item.row_count.toLocaleString()} rows</span>
          <span aria-hidden>·</span>
          <span className="tabular">{formatBytes(item.file_size)}</span>
          <span aria-hidden>·</span>
          <span className="tabular">{relativeTime(item.created_at)}</span>
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
            aria-label="Export actions"
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
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirm(true)}>
            <Trash2 className="size-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename CSV</DialogTitle>
            <DialogDescription>Give this CSV a clearer title.</DialogDescription>
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
            placeholder="CSV title"
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

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this CSV?</AlertDialogTitle>
            <AlertDialogDescription>
              "{item.title}" will be removed from the library. The original conversation is unaffected.
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
