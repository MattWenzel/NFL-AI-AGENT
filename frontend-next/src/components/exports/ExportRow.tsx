import { useState } from 'react'
import { MoreHorizontal, Trash2 } from 'lucide-react'
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
}

export function ExportRow({ item, active, onPick, onDelete }: ExportRowProps) {
  const [confirm, setConfirm] = useState(false)

  const remove = async () => {
    try {
      await onDelete(item.id)
      toast.success('CSV deleted')
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
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirm(true)}>
            <Trash2 className="size-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

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
