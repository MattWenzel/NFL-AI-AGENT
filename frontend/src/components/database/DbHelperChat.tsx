import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { useDbHelperChat } from '@/lib/dbHelperChat'

import { HelperComposer } from './HelperComposer'
import { HelperMessageList } from './HelperMessageList'

interface DbHelperChatProps {
  /** The helper hook is owned by App.tsx so its state survives the
   *  inspector closing/reopening; this component only renders. */
  helper: ReturnType<typeof useDbHelperChat>
}

export function DbHelperChat({ helper }: DbHelperChatProps) {
  const canClear = helper.messages.length > 0 && !helper.streaming

  return (
    <div className="flex h-full min-h-0 flex-col">
      {canClear ? (
        <div className="flex shrink-0 items-center justify-end border-b border-border px-3 py-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 text-xs text-muted-foreground"
            onClick={helper.clear}
            aria-label="Clear helper chat"
          >
            <Trash2 className="size-3.5" />
            Clear
          </Button>
        </div>
      ) : null}
      <HelperMessageList
        messages={helper.messages}
        streaming={helper.streaming}
        error={helper.error}
      />
      <HelperComposer
        streaming={helper.streaming}
        placeholder="Ask about the schema, plan a query, or paste SQL…"
        onSend={(message, opts) => helper.send(message, opts)}
        onStop={helper.stop}
      />
    </div>
  )
}
