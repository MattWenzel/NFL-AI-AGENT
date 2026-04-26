import { cn } from '@/lib/utils'
import type { TurnRecord } from '@/lib/types'

interface UserTurnProps {
  turn: TurnRecord
  selected?: boolean
  onSelect?: () => void
}

/**
 * User message — right-aligned bubble, like Claude.ai / ChatGPT / iMessage.
 * Position alone signals "user", so we drop the eyebrow label. Max-width
 * caps the bubble at a comfortable line length on wide screens. Clicking
 * the bubble selects the surrounding exchange so the inspector can scope
 * itself to this Q/A pair.
 */
export function UserTurn({ turn, selected = false, onSelect }: UserTurnProps) {
  return (
    <div className="flex justify-end">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onSelect?.()
        }}
        className={cn(
          'max-w-2xl rounded-2xl rounded-tr-md bg-secondary px-4 py-2.5 text-left text-secondary-foreground transition-shadow',
          'cursor-pointer hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          selected && 'ring-2 ring-accent/60',
        )}
      >
        <p className="whitespace-pre-wrap text-base leading-snug">{turn.text}</p>
      </button>
    </div>
  )
}
