import { Component, type ErrorInfo, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'

import { Button } from '@/components/ui/button'

/**
 * Failure containment for a UI surface. One render exception inside a
 * surface (a malformed markdown table, a bad tool payload) used to
 * white-screen the whole app; with a boundary per surface the rest of
 * the workspace keeps working and the user gets a reload affordance.
 *
 * Errors are forwarded to Sentry when a DSN was baked in at build time
 * (VITE_SENTRY_DSN); capture is a no-op otherwise.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null }
> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    Sentry.captureException(error, { extra: { componentStack: info.componentStack } })
  }

  render() {
    if (this.state.error) {
      return (
        <div className="grid h-full min-h-48 place-items-center p-8">
          <div className="flex max-w-sm flex-col items-center gap-3 text-center">
            <p className="text-sm font-medium">
              {this.props.label ? `${this.props.label} hit a snag.` : 'Something went wrong.'}
            </p>
            <p className="text-xs text-muted-foreground">
              {this.state.error.message}
            </p>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => this.setState({ error: null })}
              >
                Try again
              </Button>
              <Button size="sm" onClick={() => window.location.reload()}>
                Reload app
              </Button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
