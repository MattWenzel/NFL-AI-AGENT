import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { GoogleIcon } from '@/components/icons/BrandIcons'
import { apiDelete, apiFetch, apiGet, apiPost, ApiError } from '@/lib/api'
import type { AuthUser } from '@/lib/state/auth'
import { relativeTime } from '@/lib/datetime'
import type { IdentitySummary } from '@/lib/api/settings'

interface AccountTabProps {
  user: AuthUser
  onAccountDeleted: () => void
}

export function AccountTab({ user, onAccountDeleted }: AccountTabProps) {
  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h3 className="text-base font-semibold">Email</h3>
        <p className="text-sm text-muted-foreground">{user.email}</p>
      </section>

      <ChangePasswordSection />

      <LinkedAccountsSection />

      <DeleteAccountSection onDeleted={onAccountDeleted} />
    </div>
  )
}

function LinkedAccountsSection() {
  const [identities, setIdentities] = useState<IdentitySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const list = await apiGet<IdentitySummary[]>('/settings/identities')
      setIdentities(list)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not load linked accounts')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const linkGoogle = async () => {
    setBusy('google-link')
    try {
      const res = await apiPost<{ auth_url: string }>('/settings/identities/google/link')
      window.location.href = res.auth_url
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not start Google link'
      toast.error(msg)
      setBusy(null)
    }
  }

  const unlink = async (provider: string) => {
    setBusy(`unlink-${provider}`)
    try {
      await apiDelete(`/settings/identities/${provider}`)
      toast.success('Account unlinked')
      refresh()
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not unlink'
      toast.error(msg)
    } finally {
      setBusy(null)
    }
  }

  const googleIdentity = identities.find((i) => i.provider === 'google')
  const hasGoogle = !!googleIdentity

  return (
    <section className="flex flex-col gap-3 border-t border-border pt-6">
      <h3 className="text-base font-semibold">Linked accounts</h3>
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
          <div className="flex items-baseline gap-3">
            <GoogleIcon className="size-4 self-center" />
            <h4 className="text-sm font-semibold">Google</h4>
            {hasGoogle ? (
              <span className="flex items-center gap-1 text-2xs text-accent">
                <ShieldCheck className="size-3.5" />
                Linked
                {googleIdentity?.linked_at ? (
                  <span className="text-muted-foreground">
                    {' · '}{relativeTime(googleIdentity.linked_at)}
                  </span>
                ) : null}
              </span>
            ) : (
              <span className="text-2xs text-muted-foreground">Not linked</span>
            )}
          </div>
          <p className="text-2xs text-muted-foreground">
            Sign in with your Google account. Linking lets you sign in either with password
            or with Google going forward.
          </p>
          {hasGoogle ? (
            <div className="flex flex-col gap-1">
              <p className="text-sm text-foreground">{googleIdentity?.display}</p>
              <div className="flex gap-2 pt-2">
                {googleIdentity?.removable ? (
                  <Button
                    variant="ghost"
                    onClick={() => unlink('google')}
                    disabled={busy === 'unlink-google'}
                  >
                    {busy === 'unlink-google' ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      'Unlink'
                    )}
                  </Button>
                ) : (
                  <p className="text-2xs text-muted-foreground">
                    This is your only sign-in method — set a password before unlinking.
                  </p>
                )}
              </div>
            </div>
          ) : (
            <Button variant="secondary" onClick={linkGoogle} disabled={busy === 'google-link'}>
              {busy === 'google-link' ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                'Link Google account'
              )}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}

function ChangePasswordSection() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (next !== confirmPw) {
      setError('New passwords do not match')
      return
    }
    setBusy(true)
    try {
      await apiFetch('/auth/password', {
        method: 'PUT',
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      toast.success('Password updated')
      setCurrent('')
      setNext('')
      setConfirmPw('')
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not change password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">Change password</h3>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <Label htmlFor="current-password" className="text-xs">Current password</Label>
          <Input
            id="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
            disabled={busy}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="new-password" className="text-xs">New password</Label>
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            minLength={8}
            required
            disabled={busy}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="confirm-password" className="text-xs">Confirm new password</Label>
          <Input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            minLength={8}
            required
            disabled={busy}
          />
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <Button type="submit" disabled={busy || !current || !next || !confirmPw}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : 'Update password'}
        </Button>
      </form>
    </section>
  )
}

function DeleteAccountSection({ onDeleted }: { onDeleted: () => void }) {
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await apiFetch('/auth/me', {
        method: 'DELETE',
        body: JSON.stringify({ password }),
      })
      toast.success('Account deleted')
      onDeleted()
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Could not delete account')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-3 border-t border-border pt-6">
      <h3 className="text-base font-semibold">Delete account</h3>
      <p className="text-sm text-muted-foreground">
        Permanently remove your account and all conversations and CSVs.
        This cannot be undone.
      </p>
      <Button variant="destructive" onClick={() => setOpen(true)}>
        Delete account…
      </Button>

      <AlertDialog
        open={open}
        onOpenChange={(v) => {
          setOpen(v)
          if (!v) {
            setPassword('')
            setError(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this account?</AlertDialogTitle>
            <AlertDialogDescription>
              All conversations and exported CSVs will be removed. Confirm by entering your password.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="delete-password" className="text-xs">Password</Label>
            <Input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error ? <p className="text-xs text-destructive">{error}</p> : null}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={(e) => {
                e.preventDefault()
                submit()
              }}
              disabled={busy || !password}
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : 'Delete account'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
