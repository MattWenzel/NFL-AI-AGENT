import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface ComposerProps {
  disabled?: boolean
  streaming?: boolean
  /**
   * "docked" — pinned to the bottom of an active conversation. The thread
   *   above scrolls behind a translucent surface for a soft fade rather
   *   than a hard divider line.
   * "centered" — used on the empty/new-chat state alongside the prompt
   *   headline; flows in normal layout, no backdrop, no divider.
   */
  variant?: 'docked' | 'centered'
  onSend: (message: string, options: { provider: string; model: string; toolChoice: 'auto' | 'required' | 'none' }) => void
  onStop?: () => void
}

const PROVIDERS = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'codex', label: 'Codex' },
] as const

const MODELS_BY_PROVIDER: Record<string, { value: string; label: string }[]> = {
  anthropic: [
    { value: 'claude-sonnet-4-6', label: 'claude-sonnet-4-6' },
    { value: 'claude-opus-4-7', label: 'claude-opus-4-7' },
  ],
  openai: [
    { value: 'gpt-5', label: 'gpt-5' },
  ],
  codex: [
    { value: 'gpt-5', label: 'gpt-5 (codex)' },
  ],
}

const TOOL_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'required', label: 'Force tool' },
  { value: 'none', label: 'Text only' },
] as const

export function Composer({
  disabled = false,
  streaming = false,
  variant = 'docked',
  onSend,
  onStop,
}: ComposerProps) {
  const [value, setValue] = useState('')
  const [provider, setProvider] = useState<string>('anthropic')
  const [model, setModel] = useState<string>('claude-sonnet-4-6')
  const [toolChoice, setToolChoice] = useState<'auto' | 'required' | 'none'>('auto')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [value])

  // When provider changes, snap to its first model unless the current model
  // is already valid for the new provider.
  useEffect(() => {
    const valid = MODELS_BY_PROVIDER[provider] ?? []
    if (!valid.some((m) => m.value === model) && valid.length > 0) {
      setModel(valid[0].value)
    }
  }, [provider, model])

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled || streaming) return
    onSend(trimmed, { provider, model, toolChoice })
    setValue('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const models = MODELS_BY_PROVIDER[provider] ?? []

  return (
    <div
      className={cn(
        'px-6 lg:px-10',
        variant === 'docked' && 'pb-4 pt-8',
        variant === 'centered' && 'pb-2 pt-0',
      )}
    >
      <div className="mx-auto w-full max-w-5xl">
        <div className="rounded-2xl border border-border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a player, season, matchup, or matchup history..."
            rows={1}
            disabled={disabled || streaming}
            className="block w-full resize-none bg-transparent px-4 pt-3 text-base leading-snug placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="Message"
          />
          <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
            <Select value={provider} onValueChange={setProvider} disabled={streaming}>
              <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDERS.map((p) => (
                  <SelectItem key={p.value} value={p.value} className="text-xs">
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={model} onValueChange={setModel} disabled={streaming || models.length === 0}>
              <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m.value} value={m.value} className="text-xs">
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={toolChoice}
              onValueChange={(v) => setToolChoice(v as 'auto' | 'required' | 'none')}
              disabled={streaming}
            >
              <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TOOL_CHOICES.map((c) => (
                  <SelectItem key={c.value} value={c.value} className="text-xs">
                    {c.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {streaming ? (
              <Button
                type="button"
                variant="secondary"
                size="icon"
                onClick={onStop}
                className="ml-auto size-8 rounded-full"
                aria-label="Stop"
              >
                <Square className="size-3.5" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={submit}
                disabled={disabled || !value.trim()}
                className="ml-auto size-8 rounded-full"
                aria-label="Send"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
        <p className="mt-2 text-center text-2xs text-muted-foreground">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  )
}
