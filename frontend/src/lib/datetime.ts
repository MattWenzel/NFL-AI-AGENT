/**
 * Lightweight time formatters — avoids pulling in date-fns just for sidebar
 * conversation rows.
 */
export function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function absoluteTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  // "Apr 19, 12:02 AM" style — matches the old vanilla frontend.
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function ageDays(iso: string | null): number {
  if (!iso) return Infinity
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return Infinity
  return (Date.now() - d.getTime()) / 86_400_000
}
