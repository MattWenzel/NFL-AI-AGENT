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
  defaultTitle: string
  onCreated: (conversationId: string) => void
}

export function SaveAsReportDialog({
  open,
  onOpenChange,
  sql,
  result,
  defaultTitle,
  onCreated,
}: SaveAsReportDialogProps) {
  const [title, setTitle] = useState(defaultTitle)
  const [busy, setBusy] = useState(false)

  // Reset the title field whenever the dialog reopens with a new default,
  // and clear `busy` between opens.
  useEffect(() => {
    if (open) {
      setTitle(defaultTitle)
      setBusy(false)
    }
  }, [open, defaultTitle])

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
        <div className="space-y-2">
          <label htmlFor="report-title" className="text-sm font-medium">
            Title
          </label>
          <Input
            id="report-title"
            value={title}
            autoFocus
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
