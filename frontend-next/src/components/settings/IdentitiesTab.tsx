import { useCallback, useEffect, useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { GoogleIcon } from '@/components/icons/BrandIcons'
import { apiDelete, apiGet, apiPost, ApiError } from '@/lib/api'
import { relativeTime } from '@/lib/datetime'
import type { IdentitySummary } from '@/lib/settings'

export function IdentitiesTab() {
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
      setError(e instanceof ApiError ? e.detail : 'Could not load linked identities')
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
      toast.success('Identity unlinked')
      refresh()
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Could not unlink'
      toast.error(msg)
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading identities…</p>
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>
  }

  const hasGoogle = identities.some((i) => i.provider === 'google')
  const googleIdentity = identities.find((i) => i.provider === 'google')

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div className="flex items-baseline gap-3">
          <GoogleIcon className="size-4 self-center" />
          <h3 className="text-base font-semibold">Google</h3>
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
                  {busy === 'unlink-google' ? <Loader2 className="size-4 animate-spin" /> : 'Unlink'}
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
    </div>
  )
}
