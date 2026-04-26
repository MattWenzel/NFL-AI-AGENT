import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface AuthWallProps {
  onLogin: (email: string, password: string) => Promise<void>
  onRegister: (email: string, password: string, inviteCode?: string) => Promise<void>
  errorMessage?: string
}

export function AuthWall({ onLogin, onRegister, errorMessage }: AuthWallProps) {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-2 text-center">
          <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
            NFL Stats
          </p>
          <h1 className="font-display text-3xl font-medium tracking-tight">
            {mode === 'signin' ? 'Welcome back.' : 'Create an account.'}
          </h1>
          <p className="text-sm text-muted-foreground">
            Ask the agent in plain English. Get answers, reasoning, and the underlying data.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
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
          <div className="space-y-2">
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
            <div className="space-y-2">
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

        <Button variant="secondary" className="w-full" asChild>
          <a href="/auth/oauth/google/start">Continue with Google</a>
        </Button>

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
