import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, Copy, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { GoogleIcon, OpenAIIcon } from '@/components/icons/BrandIcons'
import logoUrl from '@/assets/logo.png'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, apiDelete, apiGet, apiPost } from '@/lib/api'

interface AuthWallProps {
  onLogin: (email: string, password: string) => Promise<void>
  onRegister: (email: string, password: string, inviteCode?: string) => Promise<void>
  errorMessage?: string
}

interface ChatGPTSigninStart {
  pending_id: string
  user_code: string
  verification_url: string
  expires_in: number
}

interface ChatGPTSigninStatus {
  status: 'pending' | 'complete' | 'expired' | 'error'
  email: string | null
  error: string | null
}

// Google OAuth failures arrive as a redirect to /?oauth_error=<code>
// (backend/api/routes/oauth_google.py). Map codes to banner copy here.
const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  account_unverified:
    'An account with this email already exists but hasn’t verified the address. ' +
    'Sign in with your password instead, then link Google from Settings.',
  email_unverified:
    'Your Google account email isn’t verified. Verify it with Google first.',
  oauth_disabled: 'Google sign-in is not configured on this deployment.',
  cancelled: 'Google sign-in was cancelled.',
}
const OAUTH_ERROR_FALLBACK = 'Google sign-in failed — try again.'

export function AuthWall({ onLogin, onRegister, errorMessage }: AuthWallProps) {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ChatGPT sign-in (device-code) state.
  const [chatgptStarting, setChatgptStarting] = useState(false)
  const [chatgptFlow, setChatgptFlow] = useState<ChatGPTSigninStart | null>(null)
  const [chatgptMsg, setChatgptMsg] = useState<string | null>(null)
  const [codeCopied, setCodeCopied] = useState(false)
  const pollRef = useRef<number | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }
  useEffect(() => stopPolling, [])

  // Surface OAuth redirect errors once, then scrub the param so a refresh
  // doesn't re-show a stale banner.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('oauth_error')
    if (!code) return
    setError(OAUTH_ERROR_MESSAGES[code] ?? OAUTH_ERROR_FALLBACK)
    params.delete('oauth_error')
    const qs = params.toString()
    window.history.replaceState(
      null,
      '',
      qs ? `${window.location.pathname}?${qs}` : window.location.pathname,
    )
  }, [])

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      setCodeCopied(true)
      setTimeout(() => setCodeCopied(false), 1500)
    } catch {
      toast.error('Could not copy — select the code manually')
    }
  }

  const startChatGPT = async () => {
    setChatgptStarting(true)
    setChatgptMsg(null)
    try {
      const res = await apiPost<ChatGPTSigninStart>('/auth/oauth/openai/start')
      setChatgptFlow(res)
      window.open(res.verification_url, '_blank', 'noopener,noreferrer')
      pollRef.current = window.setInterval(async () => {
        try {
          // Status returns either a status snapshot or the full token+user
          // payload on completion. The completion shape carries `token`.
          const s = await apiGet<ChatGPTSigninStatus & { token?: string }>(
            `/auth/oauth/openai/status?pending_id=${encodeURIComponent(res.pending_id)}`,
          )
          if (s.status === 'complete') {
            stopPolling()
            setChatgptFlow(null)
            // Cookies are already set by the status response; refresh so the
            // App's auth bootstrap re-runs against the new session.
            window.location.reload()
          } else if (s.status === 'expired') {
            stopPolling()
            setChatgptFlow(null)
            setChatgptMsg('Code expired — try again.')
          } else if (s.status === 'error') {
            stopPolling()
            setChatgptFlow(null)
            setChatgptMsg(s.error ?? 'Sign-in failed — try again.')
          }
        } catch (e) {
          stopPolling()
          setChatgptFlow(null)
          setChatgptMsg(e instanceof ApiError ? e.detail : 'Polling failed')
        }
      }, 2500)
    } catch (e) {
      setChatgptMsg(e instanceof ApiError ? e.detail : 'Could not start sign-in')
    } finally {
      setChatgptStarting(false)
    }
  }

  const cancelChatGPT = async () => {
    if (!chatgptFlow) return
    stopPolling()
    try {
      await apiDelete(
        `/auth/oauth/openai/cancel?pending_id=${encodeURIComponent(chatgptFlow.pending_id)}`,
      )
    } catch {
      // already cleaned up server-side; ignore
    }
    setChatgptFlow(null)
    setChatgptMsg('Cancelled.')
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setPending(true)
    try {
      if (mode === 'signin') {
        await onLogin(email, password)
      } else {
        await onRegister(email, password, invite || undefined)
      }
    } catch (e) {
      setError((e as Error).message ?? 'Something went wrong')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="grid min-h-dvh place-items-center bg-background px-6">
      <div className="flex w-full max-w-sm flex-col gap-8">
        <div className="flex flex-col gap-2 text-center">
          <div className="flex flex-col items-center gap-2">
            <img
              src={logoUrl}
              alt=""
              aria-hidden="true"
              className="size-32 object-contain"
            />
            <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
              NFL StatSource
            </p>
          </div>
          <h1 className="font-display text-3xl font-medium tracking-tight">
            {mode === 'signin' ? 'Welcome back.' : 'Create an account.'}
          </h1>
          <p className="text-sm text-muted-foreground">
            Ask the agent in plain English. Get answers, reasoning, and the underlying data.
          </p>
        </div>

        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="email" className="text-xs">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="password" className="text-xs">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              minLength={mode === 'signup' ? 8 : 1}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {mode === 'signup' ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="invite" className="text-xs">
                Invite code <span className="text-muted-foreground">(if required)</span>
              </Label>
              <Input
                id="invite"
                type="text"
                autoComplete="off"
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
              />
            </div>
          ) : null}

          {(error || errorMessage) ? (
            <p className="text-xs text-destructive" role="alert">
              {error ?? errorMessage}
            </p>
          ) : null}

          <Button type="submit" disabled={pending} className="w-full">
            {pending ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <div className="relative">
          <div className="absolute inset-0 flex items-center" aria-hidden>
            <span className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-2xs uppercase tracking-[0.14em] text-muted-foreground">
            <span className="bg-background px-2">or</span>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Button variant="secondary" className="w-full gap-2" asChild>
            <a href="/auth/oauth/google/start">
              <GoogleIcon />
              Continue with Google
            </a>
          </Button>
          {chatgptFlow ? (
            <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 px-3 py-3 text-left">
              <p className="text-xs">
                Visit{' '}
                <a
                  href={chatgptFlow.verification_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline decoration-accent decoration-2 underline-offset-4 hover:text-accent"
                >
                  {chatgptFlow.verification_url}
                </a>
                {' '}and enter:
              </p>
              <div className="flex items-center gap-2">
                <p className="font-mono text-xl font-semibold tracking-[0.2em]">{chatgptFlow.user_code}</p>
                <button
                  type="button"
                  onClick={() => copyCode(chatgptFlow.user_code)}
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
              <Button variant="ghost" size="sm" onClick={cancelChatGPT}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              variant="secondary"
              className="w-full gap-2"
              onClick={startChatGPT}
              disabled={chatgptStarting}
            >
              {chatgptStarting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <>
                  <OpenAIIcon />
                  Continue with ChatGPT
                </>
              )}
            </Button>
          )}
          {chatgptMsg ? (
            <p className="text-xs text-muted-foreground" role="status">{chatgptMsg}</p>
          ) : null}
        </div>

        <div className="text-center text-xs text-muted-foreground">
          {mode === 'signin' ? (
            <button
              type="button"
              className="underline-offset-4 hover:text-foreground hover:underline"
              onClick={() => {
                setMode('signup')
                setError(null)
              }}
            >
              Don't have an account? Sign up.
            </button>
          ) : (
            <button
              type="button"
              className="underline-offset-4 hover:text-foreground hover:underline"
              onClick={() => {
                setMode('signin')
                setError(null)
              }}
            >
              Already have an account? Sign in.
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
