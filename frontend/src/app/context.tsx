import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, refreshSession, request, setAccessToken } from '../api/client'
import type { SessionResponse, User } from '../api/types'

interface AuthValue { user: User | null; loading: boolean; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void> }
const AuthContext = createContext<AuthValue | null>(null)
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('AuthProvider is missing'); return value }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const queryClient = useQueryClient()
  useEffect(() => {
    let active = true
    void refreshSession().then((session) => { if (active) setUser(session?.user ?? null) }).catch(() => { if (active) setUser(null) }).finally(() => { if (active) setLoading(false) })
    const expired = () => { setUser(null); queryClient.clear() }
    window.addEventListener('ledgerflow:session-expired', expired)
    return () => { active = false; window.removeEventListener('ledgerflow:session-expired', expired) }
  }, [queryClient])
  async function login(email: string, password: string) {
    const session = await request<SessionResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }, false)
    queryClient.clear(); setAccessToken(session.access_token); setUser(session.user)
  }
  async function logout() {
    try { await request('/auth/logout', { method: 'POST', body: '{}' }) }
    finally { setAccessToken(null); setUser(null); queryClient.clear() }
  }
  return <AuthContext value={{ user, loading, login, logout }}>{children}</AuthContext>
}

interface Toast { id: number; message: string; kind: 'success' | 'error' }
const ToastContext = createContext<((message: string, kind?: Toast['kind']) => void) | null>(null)
export function useToast() { const value = useContext(ToastContext); if (!value) throw new Error('ToastProvider is missing'); return value }
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  function notify(message: string, kind: Toast['kind'] = 'success') {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current.slice(-3), { id, message, kind }])
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 6500)
  }
  return <ToastContext value={notify}>{children}<div className="toast-stack" aria-live="polite">{toasts.map((toast) => <div className={`toast ${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'} key={toast.id}><span>{toast.message}</span><button aria-label="Dismiss notification" onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}>×</button></div>)}</div></ToastContext>
}

export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : 'An unexpected error occurred.' }

export function useAction<T = unknown>(path: string, successMessage: string, onSuccess?: (data: T) => void) {
  const notify = useToast()
  const client = useQueryClient()
  const key = useRef<{ signature: string; value: string } | null>(null)
  return useMutation<T, Error, unknown>({
    mutationFn: (body) => {
      const serialized = JSON.stringify(body ?? {})
      const signature = path + serialized
      if (key.current?.signature !== signature) key.current = { signature, value: crypto.randomUUID() }
      return request<T>(path, { method: 'POST', body: serialized, headers: { 'Idempotency-Key': key.current.value } })
    },
    onSuccess: (data) => { key.current = null; notify(successMessage); void client.invalidateQueries(); onSuccess?.(data) },
    onError: (error) => { notify(errorMessage(error), 'error'); if (error instanceof ApiError && error.correlationId) console.info('Request reference:', error.correlationId) },
    retry: false,
  })
}
