import { cn } from '@/lib/utils'

export function ToolPayload({
  label,
  value,
  variant = 'default',
}: {
  label: string
  value: string
  variant?: 'default' | 'error' | 'muted'
}) {
  return (
    <div className="space-y-1">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <pre
        className={cn(
          'overflow-x-auto whitespace-pre-wrap rounded-md bg-card px-3 py-2 font-mono text-xs leading-relaxed',
          variant === 'error' && 'text-destructive',
          variant === 'muted' && 'text-muted-foreground',
        )}
      >
        {value}
      </pre>
    </div>
  )
}

export function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}
