import { useEffect, useRef } from 'react'

interface UseScrollToBottomOptions {
  /** Identifier that should *trigger* the scroll. Pass something that
   *  changes once per logical event (e.g. `lastUserTurnId`, the count
   *  of user messages) — not the full message list, since that fires
   *  on every streaming delta and would jitter the viewport. */
  trackedKey: string | null
  /** Identifier for the surface itself (session id, "helper", etc.).
   *  When this changes, the next scroll fires *instantly* instead of
   *  smoothly — appropriate for conversation switches and the very
   *  first paint, where a smooth scroll across a tall transcript is
   *  jarring. Pass `null` to always scroll smoothly. */
  resetKey: string | null
  /** Returns the element to scroll into view. Wrapped in a ref so the
   *  hook doesn't re-fire when the parent re-renders with a new closure. */
  getElement: () => HTMLElement | null
  /** `start` keeps the user's most recent message at the top of the
   *  viewport (default — used by the main thread). `end` keeps the
   *  trailing element flush against the bottom (used by the helper
   *  chat, whose tail is a zero-height anchor). */
  block?: ScrollLogicalPosition
}

/**
 * Smooth-on-send / instant-on-switch scroll behavior for chat surfaces.
 *
 * Lifted from `Thread.tsx`'s scroll logic so the Database helper chat
 * (and any future chat surface) can share the same UX without
 * re-implementing the prev-key tracking. Replaces the naive pattern of
 * calling `scrollIntoView` from a useEffect keyed on the message list,
 * which fires on every streaming text delta and visibly jitters.
 */
export function useScrollToBottom({
  trackedKey,
  resetKey,
  getElement,
  block = 'start',
}: UseScrollToBottomOptions) {
  const prevResetRef = useRef<string | null>(null)
  // Always-fresh ref so the effect's deps stay narrow — we don't want
  // a new `getElement` closure on every parent render to retrigger us.
  const getElementRef = useRef(getElement)
  getElementRef.current = getElement

  useEffect(() => {
    if (!trackedKey) return
    const el = getElementRef.current()
    if (!el) return
    const isFreshLoad = prevResetRef.current !== resetKey
    prevResetRef.current = resetKey
    el.scrollIntoView({
      behavior: isFreshLoad ? 'auto' : 'smooth',
      block,
    })
  }, [trackedKey, resetKey, block])
}
