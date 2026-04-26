import type { TurnRecord } from '@/lib/types'

interface UserTurnProps {
  turn: TurnRecord
}

/**
 * User message — non-bubble, document-feel, with a hairline divider above.
 * The "user voice" is signaled by a left border accent + condensed leading,
 * not a chat-bubble background.
 */
export function UserTurn({ turn }: UserTurnProps) {
  return (
    <div className="border-l-2 border-accent/60 pl-4 py-1">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground mb-1">
        You
      </p>
      <p className="whitespace-pre-wrap text-base leading-snug text-foreground">{turn.text}</p>
    </div>
  )
}
