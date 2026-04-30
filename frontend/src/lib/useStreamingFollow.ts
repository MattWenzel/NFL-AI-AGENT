import { useEffect, useRef, type RefObject } from 'react'

interface UseStreamingFollowOptions {
  /** The scroll container — typically the `overflow-y-auto` element
   *  that wraps the message list. */
  containerRef: RefObject<HTMLElement | null>
  /** Pause auto-follow entirely when false. Default true. */
  enabled?: boolean
  /** Distance from the bottom (in px) that still counts as "pinned".
   *  Anything farther up than this and the user is treated as having
   *  scrolled away to read history; auto-follow pauses. */
  threshold?: number
}

/**
 * Sticky-bottom auto-scroll for chat surfaces. Watches the scroll
 * container's first child via `ResizeObserver` and keeps the bottom in
 * view as content grows — so streaming agent text follows in real
 * time. Pauses automatically when the user scrolls up to read; resumes
 * when they scroll back down to the bottom.
 *
 * Complements `useScrollToBottom`, which handles the deliberate
 * jumps (on send, on conversation switch). This one handles the
 * second-by-second growth during streaming.
 */
export function useStreamingFollow({
  containerRef,
  enabled = true,
  threshold = 80,
}: UseStreamingFollowOptions) {
  // Initialized true so the first content-grow follows. Any user scroll
  // event will recompute it from real positions before we ever look.
  const pinnedRef = useRef(true)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight
      pinnedRef.current = distance <= threshold
    }
    update()
    el.addEventListener('scroll', update, { passive: true })
    return () => el.removeEventListener('scroll', update)
  }, [containerRef, threshold])

  useEffect(() => {
    if (!enabled) return
    const el = containerRef.current
    if (!el) return
    // ResizeObserver on the *child* picks up scrollHeight growth as the
    // message list expands. Observing the container itself only fires
    // on its own clientHeight change, which isn't what we care about.
    const child = el.firstElementChild
    if (!(child instanceof HTMLElement)) return
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) el.scrollTop = el.scrollHeight
    })
    observer.observe(child)
    return () => observer.disconnect()
  }, [containerRef, enabled])
}
