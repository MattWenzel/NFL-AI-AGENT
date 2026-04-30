import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { saveQueryAsReport, type DatabaseQueryResult } from '@/lib/database'

interface SaveAsReportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sql: string
  result: DatabaseQueryResult | null
  onCreated: (conversationId: string) => void
}

export function SaveAsReportDialog({
  open,
  onOpenChange,
  sql,
  result,
  onCreated,
}: SaveAsReportDialogProps) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)

  // Reset the title field every time the dialog opens — defaults pulled
  // from the dropdown selection rarely match the SQL the user actually
  // ran (the dropdown is just a starting-point picker).
  useEffect(() => {
    if (open) {
      setTitle('')
      setBusy(false)
    }
  }, [open])

  const submit = async () => {
    if (!result || busy) return
    const trimmed = title.trim()
    if (!trimmed) {
      toast.error('Give the report a title')
      return
    }
    setBusy(true)
    try {
      const { conversation_id } = await saveQueryAsReport({
        sql,
        columns: result.columns,
        rows: result.rows,
        row_count: result.row_count,
        truncated: result.truncated,
        title: trimmed,
      })
      onOpenChange(false)
      onCreated(conversation_id)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not save report'
      toast.error(message)
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Save as report</DialogTitle>
          <DialogDescription>
            Creates a Report seeded with these {result?.row_count ?? 0} rows. You'll be
            able to chat with the agent to refine it.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <label htmlFor="report-title" className="text-sm font-medium">
            Title
          </label>
          <Input
            id="report-title"
            value={title}
            autoFocus
            placeholder="e.g. Top WRs by yards per game (1999–2025)"
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !busy) {
                e.preventDefault()
                submit()
              }
            }}
            maxLength={200}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !result}>
            {busy ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
