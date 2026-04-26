import { useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
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
import { apiFetch, ApiError } from '@/lib/api'
import type { AuthUser } from '@/lib/auth'

interface AccountTabProps {
  user: AuthUser
  onAccountDeleted: () => void
}

export function AccountTab({ user, onAccountDeleted }: AccountTabProps) {
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h3 className="text-base font-semibold">Email</h3>
        <p className="text-sm text-muted-foreground">{user.email}</p>
      </section>

      <ChangePasswordSection />

      <DeleteAccountSection onDeleted={onAccountDeleted} />
    </div>
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
    <section className="space-y-3">
      <h3 className="text-base font-semibold">Change password</h3>
      <form onSubmit={submit} className="space-y-3">
        <div className="space-y-2">
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
        <div className="space-y-2">
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
        <div className="space-y-2">
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
    <section className="space-y-3 border-t border-border pt-6">
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
          <div className="space-y-2">
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
