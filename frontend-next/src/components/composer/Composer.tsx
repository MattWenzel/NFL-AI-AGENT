import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProviders } from '@/lib/providers'
import {
  DEFAULT_TABLE_MODE,
  DEFAULT_TABLE_SIZE,
  TABLE_SIZE_OPTIONS,
  type TableMode,
  type TableSize,
} from '@/lib/tables'
import { cn } from '@/lib/utils'

interface ComposerSendOptions {
  provider: string
  model: string
  toolChoice: 'auto' | 'required' | 'none'
  /** Only sent when tableChat is on. */
  tableMode?: TableMode
  /** Only sent when tableChat is on. */
  tableSize?: TableSize
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
   */
  variant?: 'docked' | 'centered'
  /**
   * When true, the composer renders the table-view chat extras: a mode
   * dropdown (Explore / Change table) and a size dropdown for the row cap.
   * Picking "Change table" forces tool_choice to "required" so the agent
   * has to call set_table this turn.
   */
  tableChat?: boolean
  onSend: (message: string, options: ComposerSendOptions) => void
  onStop?: () => void
}

const TOOL_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'required', label: 'Force tool' },
  { value: 'none', label: 'Text only' },
] as const

const TABLE_MODE_CHOICES: { value: TableMode; label: string }[] = [
  { value: 'explore', label: 'Explore' },
  { value: 'edit_table', label: 'Change table' },
]

// localStorage keys — keep the user's last picker choice across Composer
// remounts (centered → docked when starting a new chat) and page reloads.
const PROVIDER_STORAGE_KEY = 'chat-workspace.provider'
const MODEL_STORAGE_KEY = 'chat-workspace.model'
const TOOL_CHOICE_STORAGE_KEY = 'chat-workspace.tool_choice'
const TABLE_MODE_STORAGE_KEY = 'chat-workspace.table_mode'
const TABLE_SIZE_STORAGE_KEY = 'chat-workspace.table_size'

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
  tableChat = false,
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
  const [tableMode, setTableModeState] = useState<TableMode>(() => {
    const raw = readStoredString(TABLE_MODE_STORAGE_KEY)
    return raw === 'explore' || raw === 'edit_table' ? raw : DEFAULT_TABLE_MODE
  })
  const [tableSize, setTableSizeState] = useState<TableSize>(() => {
    const raw = readStoredString(TABLE_SIZE_STORAGE_KEY)
    if (raw === 'auto') return 'auto'
    const n = raw ? Number(raw) : NaN
    if (
      Number.isFinite(n) &&
      (TABLE_SIZE_OPTIONS as readonly (number | string)[]).includes(n)
    ) {
      return n as TableSize
    }
    return DEFAULT_TABLE_SIZE
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
  const setTableMode = (next: TableMode) => {
    setTableModeState(next)
    writeStoredString(TABLE_MODE_STORAGE_KEY, next)
  }
  const setTableSize = (next: TableSize) => {
    setTableSizeState(next)
    writeStoredString(TABLE_SIZE_STORAGE_KEY, String(next))
  }

  const { providers, status: providersStatus } = useProviders()
  const currentProvider = useMemo(
    () => providers.find((p) => p.name === provider) ?? null,
    [providers, provider],
  )

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 80), 240)}px`
  }, [value])

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
    if (tableChat) {
      // "Change table" turns must call set_table — force the model into a
      // tool call this turn. "Explore" turns leave tool_choice up to the user.
      const effectiveToolChoice = tableMode === 'edit_table' ? 'required' : toolChoice
      onSend(trimmed, {
        provider,
        model,
        toolChoice: effectiveToolChoice,
        tableMode,
        tableSize,
      })
    } else {
      onSend(trimmed, { provider, model, toolChoice })
    }
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
        'px-6 lg:px-10',
        variant === 'docked' && 'pb-4 pt-8',
        variant === 'centered' && 'pb-2 pt-0',
      )}
    >
      <div className="mx-auto w-full max-w-6xl">
        <div className="rounded-2xl border border-border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={
              tableChat
                ? tableMode === 'edit_table'
                  ? 'Ask the agent to build or replace the table…'
                  : 'Ask a question about the data — the table won\'t change.'
                : 'Ask about a player, season, matchup, or matchup history...'
            }
            rows={2}
            disabled={disabled || streaming}
            className="block w-full resize-none bg-transparent px-4 pb-2 pt-4 text-base leading-snug placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="Message"
          />
          <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
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
                  // Sort available first so the dropdown leads with usable picks.
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
            {!tableChat ? (
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
            ) : (
              <>
                <Select
                  value={tableMode}
                  onValueChange={(v) => setTableMode(v as TableMode)}
                  disabled={streaming}
                >
                  <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TABLE_MODE_CHOICES.map((c) => (
                      <SelectItem key={c.value} value={c.value} className="text-xs">
                        {c.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={String(tableSize)}
                  onValueChange={(v) => setTableSize(v === 'auto' ? 'auto' : (Number(v) as TableSize))}
                  disabled={streaming || tableMode !== 'edit_table'}
                >
                  <SelectTrigger size="sm" className="h-7 gap-1 border-0 bg-transparent px-2 text-xs hover:bg-muted">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TABLE_SIZE_OPTIONS.map((opt) => (
                      <SelectItem key={String(opt)} value={String(opt)} className="text-xs">
                        {opt === 'auto' ? 'Auto rows' : `${opt} rows`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </>
            )}

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
                disabled={disabled || !value.trim() || !provider || !model}
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
