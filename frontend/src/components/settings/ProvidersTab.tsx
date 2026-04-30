import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, CheckCircle2, Copy, Eye, EyeOff, Loader2, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { AnthropicIcon, OpenAIIcon } from '@/components/icons/BrandIcons'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiDelete, apiFetch, apiGet, apiPost, ApiError } from '@/lib/api'
import { relativeTime } from '@/lib/datetime'
import type {
  ApiKeyStatus,
  CodexOAuthStartResponse,
  CodexOAuthStatusResponse,
} from '@/lib/api/settings'

export function ProvidersTab() {
  const [statuses, setStatuses] = useState<ApiKeyStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const list = await apiGet<ApiKeyStatus[]>('/settings/api-keys')
      setStatuses(list)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not load providers')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading providers…</p>
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>
  }

  return (
    <div className="flex flex-col gap-5">
      {statuses.map((s) =>
        s.credential_shape === 'codex_oauth' ? (
          <CodexProviderRow key={s.provider} status={s} onChange={refresh} />
        ) : (
          <ApiKeyProviderRow key={s.provider} status={s} onChange={refresh} />
        ),
      )}
    </div>
  )
}

function brandIconFor(provider: string) {
  // Match on the provider key (e.g. 'anthropic', 'openai'). Unknown
  // providers fall through and the row renders without an icon — fine.
  if (provider === 'anthropic') return <AnthropicIcon className="size-4 self-center" />
  if (provider === 'openai') return <OpenAIIcon className="size-4 self-center" />
  return null
}

function ApiKeyProviderRow({ status, onChange }: { status: ApiKeyStatus; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (!value.trim()) return
    setBusy(true)
    try {
      await apiFetch(`/settings/api-keys/${status.provider}`, {
        method: 'PUT',
        body: JSON.stringify({ api_key: value.trim() }),
      })
      toast.success(`${status.display_name} key saved`)
      setEditing(false)
      setValue('')
      setReveal(false)
      onChange()
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not save'
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await apiFetch(`/settings/api-keys/${status.provider}`, {
        method: 'PUT',
        body: JSON.stringify({ api_key: null }),
      })
      toast.success(`${status.display_name} key removed`)
      onChange()
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not remove'
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline gap-3">
        {brandIconFor(status.provider)}
        <h3 className="text-base font-semibold">{status.display_name}</h3>
        {status.has_key ? (
          <span className="flex items-center gap-1 text-2xs text-accent">
            <CheckCircle2 className="size-3.5" />
            Key set
            {status.updated_at ? (
              <span className="text-muted-foreground"> · {relativeTime(status.updated_at)}</span>
            ) : null}
          </span>
        ) : (
          <span className="text-2xs text-muted-foreground">No key</span>
        )}
      </div>

      {editing ? (
        <div className="flex flex-col gap-2">
          <Label htmlFor={`key-${status.provider}`} className="text-xs">
            API key
          </Label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                id={`key-${status.provider}`}
                type={reveal ? 'text' : 'password'}
                autoComplete="off"
                placeholder="sk-…"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                disabled={busy}
                className="pr-9 font-mono text-sm"
              />
              <button
                type="button"
                onClick={() => setReveal((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                aria-label={reveal ? 'Hide key' : 'Show key'}
              >
                {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
            <Button onClick={save} disabled={busy || !value.trim()}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : 'Save'}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(false)
                setValue('')
                setReveal(false)
              }}
              disabled={busy}
            >
              <X className="size-4" />
            </Button>
          </div>
          <p className="text-2xs text-muted-foreground">
            Stored encrypted; the server only decrypts it when invoking the model on your behalf.
          </p>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            {status.has_key ? 'Replace key' : 'Add key'}
          </Button>
          {status.has_key ? (
            <Button variant="ghost" onClick={remove} disabled={busy}>
              <Trash2 className="size-4" />
              Remove
            </Button>
          ) : null}
        </div>
      )}
    </div>
  )
}

function CodexProviderRow({ status, onChange }: { status: ApiKeyStatus; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<CodexOAuthStartResponse | null>(null)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  const [codeCopied, setCodeCopied] = useState(false)
  const pollRef = useRef<number | null>(null)

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      setCodeCopied(true)
      setTimeout(() => setCodeCopied(false), 1500)
    } catch {
      toast.error('Could not copy — select the code manually')
    }
  }

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const start = async () => {
    setBusy(true)
    setStatusMsg(null)
    try {
      const res = await apiPost<CodexOAuthStartResponse>('/settings/oauth/codex/start')
      setPending(res)
      window.open(res.verification_url, '_blank', 'noopener,noreferrer')
      pollRef.current = window.setInterval(async () => {
        try {
          const s = await apiGet<CodexOAuthStatusResponse>(
            `/settings/oauth/codex/status?pending_id=${encodeURIComponent(res.pending_id)}`,
          )
          if (s.status === 'complete') {
            stopPolling()
            setPending(null)
            setStatusMsg(null)
            toast.success(`Codex linked${s.email ? ` (${s.email})` : ''}`)
            onChange()
          } else if (s.status === 'expired') {
            stopPolling()
            setPending(null)
            setStatusMsg('Code expired — try again.')
          } else if (s.status === 'error') {
            stopPolling()
            setPending(null)
            setStatusMsg(s.error ?? 'Authorization failed.')
          }
        } catch (e) {
          stopPolling()
          setPending(null)
          setStatusMsg(e instanceof ApiError ? e.detail : 'Polling failed')
        }
      }, 2500)
    } catch (e) {
      setStatusMsg(e instanceof ApiError ? e.detail : 'Could not start authorization')
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!pending) return
    stopPolling()
    try {
      await apiDelete(
        `/settings/oauth/codex/cancel?pending_id=${encodeURIComponent(pending.pending_id)}`,
      )
    } catch {
      // already cleaned up server-side; ignore
    }
    setPending(null)
    setStatusMsg('Cancelled.')
  }

  const remove = async () => {
    setBusy(true)
    try {
      await apiFetch(`/settings/api-keys/${status.provider}`, {
        method: 'PUT',
        body: JSON.stringify({ api_key: null }),
      })
      toast.success('Codex disconnected')
      onChange()
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not disconnect'
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline gap-3">
        <OpenAIIcon className="size-4 self-center" />
        <h3 className="text-base font-semibold">{status.display_name}</h3>
        {status.has_key ? (
          <span className="flex items-center gap-1 text-2xs text-accent">
            <CheckCircle2 className="size-3.5" />
            Connected
            {status.email ? <span className="text-muted-foreground"> · {status.email}</span> : null}
          </span>
        ) : (
          <span className="text-2xs text-muted-foreground">Not connected</span>
        )}
      </div>
      <p className="text-2xs text-muted-foreground">
        Sign in with your OpenAI account through the device-code flow. No API key required.
      </p>

      {pending ? (
        <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 px-3 py-3">
          <p className="text-xs">
            Visit{' '}
            <a
              href={pending.verification_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline decoration-accent decoration-2 underline-offset-4 hover:text-accent"
            >
              {pending.verification_url}
            </a>
            {' '}and enter:
          </p>
          <div className="flex items-center gap-2">
            <p className="font-mono text-xl font-semibold tracking-[0.2em]">{pending.user_code}</p>
            <button
              type="button"
              onClick={() => copyCode(pending.user_code)}
              aria-label="Copy code"
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {codeCopied ? <Check className="size-3" /> : <Copy className="size-3" />}
              <span>{codeCopied ? 'Copied' : 'Copy'}</span>
            </button>
          </div>
          <div className="flex items-center gap-1.5 text-2xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            Waiting for authorization…
          </div>
          <Button variant="ghost" size="sm" onClick={cancel}>
            Cancel
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          {status.has_key ? (
            <Button variant="ghost" onClick={remove} disabled={busy}>
              <Trash2 className="size-4" />
              Disconnect
            </Button>
          ) : (
            <Button variant="secondary" onClick={start} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : 'Connect with OpenAI'}
            </Button>
          )}
        </div>
      )}

      {statusMsg ? <p className="text-xs text-muted-foreground">{statusMsg}</p> : null}
    </div>
  )
}
