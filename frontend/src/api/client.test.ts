import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request, setAccessToken } from './client'

beforeEach(() => setAccessToken(null))
afterEach(() => { vi.unstubAllGlobals(); setAccessToken(null) })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
describe('authenticated API boundary', () => {
  it('refreshes an expired access token without changing the financial idempotency key', async () => {
    setAccessToken('expired-test-token')
    const fetch = vi.fn()
      .mockResolvedValueOnce(json({ error: { code: 'TOKEN_EXPIRED', message: 'Expired' } }, 401))
      .mockResolvedValueOnce(json({ access_token: 'rotated-test-token', token_type: 'bearer', user: { id: 'user', role: 'customer' } }))
      .mockResolvedValueOnce(json({ id: 'original-payment' }))
    vi.stubGlobal('fetch', fetch)
    expect(await request('/payments', { method: 'POST', headers: { 'Idempotency-Key': 'same-operation' }, body: '{"amount_minor":29}' })).toEqual({ id: 'original-payment' })
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/auth/refresh')
    for (const index of [0, 2]) {
      expect(fetch.mock.calls[index][1]).toMatchObject({ credentials: 'include', headers: { 'Idempotency-Key': 'same-operation' }, body: '{"amount_minor":29}' })
    }
    expect(fetch.mock.calls[2][1].headers.Authorization).toBe('Bearer rotated-test-token')
  })
  it('does not blindly retry a financial request when the response is lost', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('Network connection closed'))
    vi.stubGlobal('fetch', fetch)
    await expect(request('/transfers', { method: 'POST', body: '{}' })).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
    expect(fetch).toHaveBeenCalledOnce()
  })
  it('keeps authorization failures distinct from token refresh and preserves the request reference', async () => {
    const fetch = vi.fn().mockResolvedValue(json({ error: { code: 'FORBIDDEN', message: 'Role is not allowed' }, correlation_id: 'request-reference' }, 403))
    vi.stubGlobal('fetch', fetch)
    const result = request('/chaos/experiments')
    await expect(result).rejects.toBeInstanceOf(ApiError)
    await expect(result).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN', correlationId: 'request-reference' })
    expect(fetch).toHaveBeenCalledOnce()
  })
  it('reuses a concurrent refresh when an older request returns a late 401', async () => {
    setAccessToken('expired-test-token')
    let releaseLate!: (response: Response) => void
    const pendingLate = new Promise<Response>((resolve) => { releaseLate = resolve })
    const fetch = vi.fn().mockImplementation(async (path: string, options: RequestInit) => {
      if (path === '/api/v1/auth/refresh') return json({ access_token: 'rotated-test-token', token_type: 'bearer', user: { id: 'user', role: 'customer' } })
      if ((options.headers as Record<string, string>).Authorization === 'Bearer expired-test-token') {
        return path === '/api/v1/late' ? pendingLate : json({ error: { message: 'Expired' } }, 401)
      }
      return json({ path })
    })
    vi.stubGlobal('fetch', fetch)
    const first = request('/first')
    const late = request('/late')
    await expect(first).resolves.toEqual({ path: '/api/v1/first' })
    releaseLate(json({ error: { message: 'Expired' } }, 401))
    await expect(late).resolves.toEqual({ path: '/api/v1/late' })
    expect(fetch.mock.calls.filter(([path]) => path === '/api/v1/auth/refresh')).toHaveLength(1)
  })
})
