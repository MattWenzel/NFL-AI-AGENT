import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface ComposerProps {
  disabled?: boolean
  onSend?: (message: string) => void
}

const PROVIDERS = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
] as const

const TOOL_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'required', label: 'Force tool' },
  { value: 'none', label: 'Text only' },
] as const

export function Composer({ disabled = false, onSend }: ComposerProps) {
  const [value, setValue] = useState('')
  const [provider, setProvider] = useState<string>('anthropic')
  const [model, setModel] = useState<string>('claude-sonnet-4-6')
  const [toolChoice, setToolChoice] = useState<string>('auto')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  // Inline-grow the textarea up to ~10 lines, then internally scroll.
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [value])

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend?.(trimmed)
    setValue('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t border-border bg-background/95 px-4 pb-4 pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto max-w-3xl">
        <div className="rounded-2xl border border-border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a player, season, matchup, or matchup history..."
            rows={1}
            disabled={disabled}
            className="block w-full resize-none bg-transparent px-4 pt-3 text-base leading-snug placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="Message"
          />
          <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
            <Select value={provider} onValueChange={setProvider}>
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
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="claude-sonnet-4-6" className="text-xs">claude-sonnet-4-6</SelectItem>
                <SelectItem value="claude-opus-4-7" className="text-xs">claude-opus-4-7</SelectItem>
                <SelectItem value="gpt-5" className="text-xs">gpt-5</SelectItem>
              </SelectContent>
            </Select>
            <Select value={toolChoice} onValueChange={setToolChoice}>
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
          </div>
        </div>
        <p className="mt-2 text-center text-2xs text-muted-foreground">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  )
}
