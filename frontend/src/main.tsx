import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from '@/components/ErrorBoundary'

// Mirror of the backend's secret patterns (backend/server/logging.py) so a
// pasted API key that surfaces in a React error context never reaches
// Sentry in the clear.
const SECRET_PATTERNS = [
  /sk-ant-[A-Za-z0-9_-]{20,}/g,
  /sk-proj-[A-Za-z0-9_-]{20,}/g,
  /sk-[A-Za-z0-9]{20,}/g,
  /Bearer\s+[A-Za-z0-9._-]{16,}/g,
  /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g,
]

function scrubValue(value: unknown): unknown {
  if (typeof value === 'string') {
    return SECRET_PATTERNS.reduce((v, p) => v.replace(p, '[REDACTED]'), value)
  }
  if (Array.isArray(value)) return value.map(scrubValue)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, scrubValue(v)]),
    )
  }
  return value
}

// Baked in at build time (vite replaces import.meta.env statically).
// Without a DSN this is skipped and every Sentry call is a no-op.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    sendDefaultPii: false,
    beforeSend: (event) => scrubValue(event) as typeof event,
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
