import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Settings2, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProviders } from '@/lib/providers'
import { cn } from '@/lib/utils'

interface ComposerSendOptions {
  provider: string
  model: string
  toolChoice: 'auto' | 'required' | 'none'
}

interface ComposerProps {
  disabled?: boolean
  streaming?: boolean
  /**
   * "docked" — pinned to the bottom of an active conversation. The thread
   *   above scrolls behind a translucent surface for a soft fade rather
   *   than a hard divider line.
   * "centered" — used on the empty/new-chat state alongside the prompt
   *   headline; flows in normal layout, no backdrop, no divider.
   * "compact" — for narrow side panes (Reports chat). Tighter padding,
   *   no max-width cap, smaller minimum textarea height.
   */
  variant?: 'docked' | 'centered' | 'compact'
  /** Optional placeholder override — used by the table view to hint that
   *  the message will affect a shared table. */
  placeholder?: string
  onSend: (message: string, options: ComposerSendOptions) => void
  onStop?: () => void
}

const TOOL_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'required', label: 'Force tool' },
  { value: 'none', label: 'Text only' },
] as const

// localStorage keys — keep the user's last picker choice across Composer
// remounts (centered → docked when starting a new chat) and page reloads.
const PROVIDER_STORAGE_KEY = 'chat-workspace.provider'
const MODEL_STORAGE_KEY = 'chat-workspace.model'
const TOOL_CHOICE_STORAGE_KEY = 'chat-workspace.tool_choice'

function readStoredString(key: string): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStoredString(key: string, value: string | null) {
  if (typeof window === 'undefined') return
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Quota / private mode — non-fatal, persistence is a nice-to-have.
  }
}

export function Composer({
  disabled = false,
  streaming = false,
  variant = 'docked',
  placeholder,
  onSend,
  onStop,
}: ComposerProps) {
  const [value, setValue] = useState('')
  // Seed picker state from localStorage so the choice persists across the
  // Composer remount when going from EmptyThread (centered) to Thread
  // (docked), and across page reloads.
  const [provider, setProviderState] = useState<string | null>(() =>
    readStoredString(PROVIDER_STORAGE_KEY),
  )
  const [model, setModelState] = useState<string | null>(() =>
    readStoredString(MODEL_STORAGE_KEY),
  )
  const [toolChoice, setToolChoiceState] = useState<'auto' | 'required' | 'none'>(() => {
    const raw = readStoredString(TOOL_CHOICE_STORAGE_KEY)
    return raw === 'auto' || raw === 'required' || raw === 'none' ? raw : 'auto'
  })
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const setProvider = (next: string) => {
    setProviderState(next)
    writeStoredString(PROVIDER_STORAGE_KEY, next)
  }
  const setModel = (next: string) => {
    setModelState(next)
    writeStoredString(MODEL_STORAGE_KEY, next)
  }
  const setToolChoice = (next: 'auto' | 'required' | 'none') => {
    setToolChoiceState(next)
    writeStoredString(TOOL_CHOICE_STORAGE_KEY, next)
  }

  const { providers, status: providersStatus } = useProviders()
  const currentProvider = useMemo(
    () => providers.find((p) => p.name === provider) ?? null,
    [providers, provider],
  )

  // ChatGPT-style auto-grow: starts at one line, grows with content, caps
  // at 240px and scrolls inside. The compact (side-pane) variant skips
  // this — its textarea is locked at h-16 so toggling the chat pane open
  // and closed doesn't visibly resize the input.
  useEffect(() => {
    if (variant === 'compact') return
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 44), 240)}px`
  }, [value, variant])

  // Validate the persisted provider against what the server reports. If it's
  // missing or now unavailable (key revoked, registry change), fall back to
  // the first available provider — first listed if none are available.
  useEffect(() => {
    if (providers.length === 0) return
    const stored = providers.find((p) => p.name === provider)
    if (stored && stored.available) return
    const initial = providers.find((p) => p.available) ?? providers[0]
    setProviderState(initial.name)
    writeStoredString(PROVIDER_STORAGE_KEY, initial.name)
    if (!model || !initial.models.includes(model)) {
      setModelState(initial.default_model)
      writeStoredString(MODEL_STORAGE_KEY, initial.default_model)
    }
  }, [providers, provider, model])

  // When provider changes (or its model list does), snap to a valid model
  // for that provider — using its server-declared default unless the current
  // selection is already one of its supported models.
  useEffect(() => {
    if (!currentProvider) return
    if (model && currentProvider.models.includes(model)) return
    setModelState(currentProvider.default_model)
    writeStoredString(MODEL_STORAGE_KEY, currentProvider.default_model)
  }, [currentProvider, model])

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled || streaming || !provider || !model) return
    onSend(trimmed, { provider, model, toolChoice })
    setValue('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const models = currentProvider?.models ?? []

  return (
    <div
      className={cn(
        'shrink-0',
        variant === 'compact' ? 'px-3 pb-3 pt-3' : 'px-6 lg:px-10',
        variant === 'docked' && 'pb-4 pt-8',
        variant === 'centered' && 'pb-2 pt-0',
      )}
    >
      <div className={cn('mx-auto w-full', variant !== 'compact' && 'max-w-6xl')}>
        <div
          className={cn(
            'overflow-hidden border border-border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0',
            variant === 'compact' ? 'rounded-xl' : 'rounded-2xl',
          )}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder ?? 'Ask about a player, season, matchup, or matchup history...'}
            rows={1}
            disabled={disabled || streaming}
            className={cn(
              'block w-full resize-none bg-transparent leading-snug placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60',
              variant === 'compact'
                ? 'h-16 overflow-y-auto px-3 pb-1.5 pt-3 text-sm [field-sizing:fixed]'
                : 'overflow-y-auto px-4 pb-2 pt-3 text-base',
            )}
            aria-label="Message"
          />
          <div
            className={cn(
              'flex min-w-0 flex-wrap items-center gap-1.5',
              variant === 'compact' ? 'px-1.5 pb-1.5 pt-0.5' : 'px-2 pb-2 pt-1',
            )}
          >
            {variant === 'compact' ? (
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 shrink-0 text-muted-foreground hover:bg-muted"
                    aria-label="Composer settings"
                    title="Provider / model / tool choice"
                  >
                    <Settings2 className="size-3.5" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="start" side="top" className="w-64 space-y-3">
                  <div className="space-y-2">
                    <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Provider
                    </p>
                    <Select
                      value={provider ?? undefined}
                      onValueChange={setProvider}
                      disabled={providersStatus !== 'ready'}
                    >
                      <SelectTrigger size="sm" className="h-8 w-full text-xs">
                        <SelectValue
                          placeholder={providersStatus === 'loading' ? 'Loading…' : 'Provider'}
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {[...providers]
                          .sort((a, b) => Number(b.available) - Number(a.available))
                          .map((p) => (
                            <SelectItem
                              key={p.name}
                              value={p.name}
                              className="text-xs"
                              disabled={!p.available}
                            >
                              {p.display_name}
                              {!p.available ? ' — set key in Settings' : ''}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Model
                    </p>
                    <Select
                      value={model ?? undefined}
                      onValueChange={setModel}
                      disabled={models.length === 0}
                    >
                      <SelectTrigger size="sm" className="h-8 w-full text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {models.map((m) => (
                          <SelectItem key={m} value={m} className="text-xs">
                            {m}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                      Tool use
                    </p>
                    <Select
                      value={toolChoice}
                      onValueChange={(v) => setToolChoice(v as 'auto' | 'required' | 'none')}
                    >
                      <SelectTrigger size="sm" className="h-8 w-full text-xs">
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
                  </div>
                </PopoverContent>
              </Popover>
            ) : (
              <>
                <Select
                  value={provider ?? undefined}
                  onValueChange={setProvider}
                  disabled={streaming || providersStatus !== 'ready'}
                >
                  <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                    <SelectValue placeholder={providersStatus === 'loading' ? 'Loading…' : 'Provider'} />
                  </SelectTrigger>
                  <SelectContent>
                    {[...providers]
                      .sort((a, b) => Number(b.available) - Number(a.available))
                      .map((p) => (
                        <SelectItem
                          key={p.name}
                          value={p.name}
                          className="text-xs"
                          disabled={!p.available}
                        >
                          {p.display_name}
                          {!p.available ? ' — set key in Settings' : ''}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <Select
                  value={model ?? undefined}
                  onValueChange={setModel}
                  disabled={streaming || models.length === 0}
                >
                  <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m} value={m} className="text-xs">
                        {m}
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
              </>
            )}

            {streaming ? (
              <Button
                type="button"
                variant={variant === 'compact' ? 'outline' : 'secondary'}
                size="icon"
                onClick={onStop}
                className={cn('ml-auto rounded-full', variant === 'compact' ? 'h-7 w-7' : 'size-8')}
                aria-label="Stop"
              >
                <Square className="size-3.5" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={submit}
                disabled={disabled || !value.trim() || !provider || !model}
                className={cn('ml-auto rounded-full', variant === 'compact' ? 'h-7 w-7' : 'size-8')}
                aria-label="Send"
              >
                <ArrowUp className={variant === 'compact' ? 'size-3.5' : 'size-4'} />
              </Button>
            )}
          </div>
        </div>
        <p
          className={cn(
            'text-center text-2xs text-muted-foreground',
            variant === 'compact' ? 'mt-1.5' : 'mt-2',
          )}
        >
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  )
}
