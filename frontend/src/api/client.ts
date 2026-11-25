import type { SessionResponse } from './types'

let accessToken: string | null = null
let refreshPromise: Promise<SessionResponse | null> | null = null
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly correlationId: string | null
  constructor(message: string, status: number, code = 'REQUEST_FAILED', correlationId: string | null = null) {
    super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.correlationId = correlationId
  }
}
export function setAccessToken(token: string | null) { accessToken = token }

async function responseError(response: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string }; detail?: unknown; correlation_id?: string } = {}
  try { body = await response.json() } catch { /* A proxy may return a non-JSON failure. */ }
  const message = body.error?.message ?? (typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}).`)
  return new ApiError(message, response.status, body.error?.code, body.correlation_id ?? response.headers.get('x-correlation-id'))
}

export async function refreshSession(): Promise<SessionResponse | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const response = await fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      if (!response.ok) { setAccessToken(null); return null }
      const session: SessionResponse = await response.json()
      setAccessToken(session.access_token)
      return session
    })().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

async function authenticatedFetch(path: string, options: RequestInit = {}, authenticated = true): Promise<Response> {
  const initialToken = accessToken
  const perform = () => fetch(`/api/v1${path}`, {
    ...options, credentials: 'include', headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(accessToken && authenticated ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...options.headers,
    },
  })
  let response: Response
  try { response = await perform() } catch { throw new ApiError('The local API is unavailable. Check System Health or try again.', 0, 'NETWORK_ERROR') }
  if (response.status === 401 && authenticated) {
    if (accessToken && accessToken !== initialToken) {
      // A concurrent request already rotated the session while this old request
      // was in flight. Reuse its token instead of revoking it with another refresh.
      response = await perform()
    } else {
      const session = await refreshSession()
      if (session) response = await perform()
      else window.dispatchEvent(new Event('ledgerflow:session-expired'))
    }
  }
  if (!response.ok) throw await responseError(response)
  return response
}

export async function request<T>(path: string, options: RequestInit = {}, authenticated = true): Promise<T> {
  const response = await authenticatedFetch(path, options, authenticated)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function downloadCsv(path: string, filename: string) {
  const response = await authenticatedFetch(path)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click()
  URL.revokeObjectURL(url)
}
