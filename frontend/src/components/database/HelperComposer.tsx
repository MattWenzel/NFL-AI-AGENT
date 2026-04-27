import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Settings2, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useProviders } from '@/lib/providers'
import { cn } from '@/lib/utils'

interface HelperComposerSendOptions {
  provider: string
  model: string
  toolChoice: 'auto' | 'required' | 'none'
}

interface HelperComposerProps {
  streaming?: boolean
  placeholder?: string
  onSend: (message: string, options: HelperComposerSendOptions) => void
  onStop?: () => void
}

const TOOL_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'required', label: 'Force tool' },
  { value: 'none', label: 'Text only' },
] as const

// Same localStorage keys the main Composer uses — pickers stay in sync
// with whatever the user picks in the regular chat composer.
const PROVIDER_STORAGE_KEY = 'chat-workspace.provider'
const MODEL_STORAGE_KEY = 'chat-workspace.model'
const TOOL_CHOICE_STORAGE_KEY = 'chat-workspace.tool_choice'

function readStored(key: string): string | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(key: string, value: string | null) {
  if (typeof window === 'undefined') return
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // private mode / quota — non-fatal
  }
}

/** Slim composer for the SQL helper panel.
 *  Textarea + send button by default; a Settings popover tucks the
 *  provider/model/tool-choice selectors out of the way so they don't
 *  consume horizontal space in a 360px right pane. */
export function HelperComposer({
  streaming = false,
  placeholder = 'Ask the SQL helper…',
  onSend,
  onStop,
}: HelperComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const [provider, setProviderState] = useState<string | null>(() =>
    readStored(PROVIDER_STORAGE_KEY),
  )
  const [model, setModelState] = useState<string | null>(() =>
    readStored(MODEL_STORAGE_KEY),
  )
  const [toolChoice, setToolChoiceState] = useState<'auto' | 'required' | 'none'>(() => {
    const raw = readStored(TOOL_CHOICE_STORAGE_KEY)
    return raw === 'auto' || raw === 'required' || raw === 'none' ? raw : 'auto'
  })

  const setProvider = (next: string) => {
    setProviderState(next)
    writeStored(PROVIDER_STORAGE_KEY, next)
  }
  const setModel = (next: string) => {
    setModelState(next)
    writeStored(MODEL_STORAGE_KEY, next)
  }
  const setToolChoice = (next: 'auto' | 'required' | 'none') => {
    setToolChoiceState(next)
    writeStored(TOOL_CHOICE_STORAGE_KEY, next)
  }

  const { providers, status: providersStatus } = useProviders()
  const currentProvider = useMemo(
    () => providers.find((p) => p.name === provider) ?? null,
    [providers, provider],
  )

  // Validate the persisted provider against what the server reports —
  // mirrors the main Composer so the helper can't get stuck on a key
  // the user revoked elsewhere.
  useEffect(() => {
    if (providers.length === 0) return
    const stored = providers.find((p) => p.name === provider)
    if (stored && stored.available) return
    const initial = providers.find((p) => p.available) ?? providers[0]
    setProviderState(initial.name)
    writeStored(PROVIDER_STORAGE_KEY, initial.name)
    if (!model || !initial.models.includes(model)) {
      setModelState(initial.default_model)
      writeStored(MODEL_STORAGE_KEY, initial.default_model)
    }
  }, [providers, provider, model])

  useEffect(() => {
    if (!currentProvider) return
    if (model && currentProvider.models.includes(model)) return
    setModelState(currentProvider.default_model)
    writeStored(MODEL_STORAGE_KEY, currentProvider.default_model)
  }, [currentProvider, model])

  // Auto-resize within a smaller range than the main Composer.
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 36), 140)}px`
  }, [value])

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || streaming || !provider || !model) return
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
    <div className="shrink-0 px-3 pb-3 pt-1">
      <div className="rounded-2xl border border-border bg-background shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          rows={1}
          spellCheck={false}
          className={cn(
            'block w-full resize-none bg-transparent px-3 pt-2.5 pb-1 text-sm leading-snug',
            'focus:outline-none placeholder:text-muted-foreground',
          )}
        />
        <div className="flex items-center gap-1 px-1.5 pb-1.5 pt-0.5">
          <Popover>
            <PopoverTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7 shrink-0 text-muted-foreground hover:bg-muted"
                aria-label="Helper settings"
                title="Provider / model / tool choice"
              >
                <Settings2 className="size-3.5" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" side="top" className="w-64 gap-3">
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
                onValueChange={(v) =>
                  setToolChoice(v as 'auto' | 'required' | 'none')
                }
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
          <div className="ml-auto">
            {streaming ? (
              <Button
                type="button"
                size="icon"
                variant="outline"
                className="h-7 w-7 shrink-0"
                onClick={onStop}
                aria-label="Stop"
              >
                <Square className="size-3.5" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={submit}
                disabled={!value.trim()}
                aria-label="Send"
              >
                <ArrowUp className="size-3.5" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
