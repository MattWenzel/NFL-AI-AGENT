import { useCallback, useState } from 'react'

import logoUrl from '@/assets/logo.png'
import { Composer } from '@/components/composer/Composer'
import { SqlEditorPanel } from '@/components/tables/SqlEditorPanel'
import { ApiError } from '@/lib/api'
import { seedReportFromSql } from '@/lib/database'

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
  /** Called when the user seeds the new report via SQL — receives the
   *  freshly-created table_chat session id so the parent can navigate. */
  onSqlCreated: (conversationId: string) => void
}

/** Shown when the user clicks "New report" but hasn't sent the first
 *  message yet. Mirrors the regular "New chat" empty state — no API
 *  request fires until submit, at which point App.tsx creates a
 *  table_chat session and routes the message into it.
 *
 *  Below the composer, a collapsed SQL panel offers a SQL-first path:
 *  the user writes SQL, clicks Create, and the backend seeds a new
 *  Report with the result in one round trip. */
export function EmptyReportScreen({
  streaming = false,
  onSend,
  onStop,
  onSqlCreated,
}: EmptyReportScreenProps) {
  const [sql, setSql] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runSeed = useCallback(async () => {
    const trimmed = sql.trim()
    if (!trimmed || running) return
    setRunning(true)
    setError(null)
    try {
      const { conversation_id } = await seedReportFromSql({ sql: trimmed })
      onSqlCreated(conversation_id)
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Query failed'
      setError(message)
    } finally {
      setRunning(false)
    }
  }, [sql, running, onSqlCreated])

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-10 px-6 pb-24">
      <div className="flex max-w-md flex-col items-center gap-5 text-center">
        <img
          src={logoUrl}
          alt=""
          aria-hidden="true"
          className="size-32 object-contain"
        />
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
        <div className="mx-auto mt-3 w-full max-w-6xl px-6 lg:px-10">
          <SqlEditorPanel
            sql={sql}
            onSqlChange={setSql}
            onRun={runSeed}
            running={running}
            error={error}
            defaultOpen={false}
            runLabel="Create"
            runningLabel="Creating"
            collapsedHint="or write SQL directly to seed the report"
            placeholder="SELECT ..."
          />
        </div>
      </div>
    </div>
  )
}
