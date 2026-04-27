import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

import { cn } from '@/lib/utils'

export function ToolPayload({
  label,
  value,
  variant = 'default',
  fillHeight = false,
}: {
  label: string
  value: string
  variant?: 'default' | 'error' | 'muted'
  /** When true, the box stretches to fill the parent flex column instead of
   *  capping at a fixed max-height. */
  fillHeight?: boolean
}) {
  const [copied, setCopied] = useState(false)

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      // Clipboard API can fail in insecure contexts — silent fall-through;
      // the value is still selectable in the pre block.
    }
  }

  return (
    <div
      className={cn(
        'space-y-1',
        fillHeight && 'flex min-h-0 flex-1 flex-col',
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-2">
        <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </p>
        <button
          type="button"
          onClick={onCopy}
          aria-label={`Copy ${label.toLowerCase()}`}
          className="inline-flex items-center gap-1 rounded text-2xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre
        className={cn(
          'overflow-auto whitespace-pre rounded-md border border-border bg-card px-3 py-2 font-mono text-xs leading-relaxed',
          fillHeight ? 'min-h-0 flex-1' : 'max-h-80',
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
