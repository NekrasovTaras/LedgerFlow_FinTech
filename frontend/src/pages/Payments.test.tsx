import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentInspector } from './Payments'

const { requestMock, mutateMock } = vi.hoisted(() => ({ requestMock: vi.fn(), mutateMock: vi.fn() }))
vi.mock('../api/client', () => ({ request: requestMock }))
vi.mock('../app/context', () => ({
  useAction: (path: string) => ({ mutate: (body: unknown) => mutateMock(path, body), isPending: false }),
  useToast: () => vi.fn(),
  useAuth: () => ({ user: { role: 'customer' } }),
  errorMessage: (error: unknown) => error instanceof Error ? error.message : 'Request failed.',
}))

beforeEach(() => {
  mutateMock.mockReset()
  requestMock.mockImplementation(async (path: string) => path.endsWith('/timeline')
    ? { items: [], total: 0, page: 1, page_size: 20 }
    : {
      id: 'payment-1', wallet_id: 'source-1', merchant_wallet_id: 'merchant-1',
      amount_minor: 10000, currency: 'RUB', status: 'UNKNOWN', description: 'Uncertain payment',
      provider: 'alpha', provider_reference: null, risk_score: 0, risk_decision: 'ALLOW',
      refunded_minor: 0, created_at: '2026-11-25T00:00:00Z', version: 4,
    })
})

describe('unknown payment investigation', () => {
  it('resolves the original provider instead of requesting another processing attempt', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/payments/payment-1']}><Routes><Route path="/payments/:id" element={<PaymentInspector />} /></Routes></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Resolve status' }))
    expect(mutateMock).toHaveBeenCalledWith('/payments/payment-1/resolve', {})
    expect(screen.queryByRole('button', { name: 'Process payment' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel payment' })).not.toBeInTheDocument()
  })
})
