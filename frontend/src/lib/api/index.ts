/**
 * Thin fetch wrapper that round-trips the FastAPI session cookie + CSRF
 * cookie. Mutating requests pull the csrf_token cookie and echo it as the
 * X-CSRF-Token header (double-submit pattern; matches the existing
 * vanilla frontend's CSRF behavior).
 */

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  for (const part of document.cookie.split(';')) {
    const [k, v] = part.trim().split('=')
    if (k === name) return decodeURIComponent(v ?? '')
  }
  return null
}

function csrfHeaders(method: string): HeadersInit {
  if (method === 'GET' || method === 'HEAD') return {}
  const token = readCookie('csrf_token')
  return token ? { 'X-CSRF-Token': token } : {}
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
  } catch {
    // body wasn't JSON
  }
  return new ApiError(res.status, detail)
}

export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers ?? {})
  for (const [k, v] of Object.entries(csrfHeaders(method))) headers.set(k, v as string)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(input, { ...init, headers, credentials: 'same-origin' })
  if (!res.ok) throw await parseError(res)
  return res
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await apiFetch(path)
  return (await res.json()) as T
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: 'POST',
    body: body == null ? undefined : JSON.stringify(body),
  })
  return (await res.json()) as T
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
  return (await res.json()) as T
}

export async function apiDelete<T = { ok: boolean }>(path: string): Promise<T> {
  const res = await apiFetch(path, { method: 'DELETE' })
  return (await res.json()) as T
}
