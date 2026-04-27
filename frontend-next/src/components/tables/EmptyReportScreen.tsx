import { Table2 } from 'lucide-react'

import { Composer } from '@/components/composer/Composer'

interface EmptyReportScreenProps {
  streaming?: boolean
  onSend: (
    message: string,
    opts: {
      provider: string
      model: string
      toolChoice: 'auto' | 'required' | 'none'
    },
  ) => void
  onStop?: () => void
}

/** Shown when the user clicks "New report" but hasn't sent the first
 *  message yet. Mirrors the regular "New chat" empty state — no API
 *  request fires until submit, at which point App.tsx creates a
 *  table_chat session and routes the message into it. */
export function EmptyReportScreen({ streaming = false, onSend, onStop }: EmptyReportScreenProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-10 px-6 pb-24">
      <div className="flex max-w-md flex-col items-center gap-5 text-center">
        <div className="grid size-32 place-items-center rounded-2xl bg-muted/50 text-muted-foreground">
          <Table2 className="size-16" strokeWidth={1.5} />
        </div>
        <div className="space-y-4">
          <h1 className="font-display text-4xl font-medium tracking-tight">
            Build a report
          </h1>
          <p className="text-base text-muted-foreground">
            Ask the agent to build a table. The agent can also research the
            data, answer questions, and refine the table whenever it isn't
            locked.
          </p>
        </div>
      </div>
      <div className="w-full">
        <Composer
          variant="centered"
          placeholder="Ask the agent to build a table…"
          streaming={streaming}
          onSend={onSend}
          onStop={onStop}
        />
      </div>
    </div>
  )
}
