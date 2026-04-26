import type { TurnRecord } from '@/lib/types'

interface UserTurnProps {
  turn: TurnRecord
}

/**
 * User message — right-aligned bubble, like Claude.ai / ChatGPT / iMessage.
 * Position alone signals "user", so we drop the eyebrow label. Max-width
 * caps the bubble at a comfortable line length on wide screens.
 */
export function UserTurn({ turn }: UserTurnProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-2xl rounded-2xl rounded-tr-md bg-secondary px-4 py-2.5 text-secondary-foreground">
        <p className="whitespace-pre-wrap text-base leading-snug">{turn.text}</p>
      </div>
    </div>
  )
}
