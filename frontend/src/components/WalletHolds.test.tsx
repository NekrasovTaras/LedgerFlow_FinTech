import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { WalletHolds } from './WalletHolds'

const { requestMock, mutateMock, notifyMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  mutateMock: vi.fn(),
  notifyMock: vi.fn(),
}))

vi.mock('../api/client', () => ({ request: requestMock }))
vi.mock('../app/context', () => ({
  useAction: (path: string) => ({ mutate: (body: unknown) => mutateMock(path, body), isPending: false }),
  useToast: () => notifyMock,
  errorMessage: (error: unknown) => error instanceof Error ? error.message : 'Request failed.',
}))

const wallet = {
  id: 'wallet-1', name: 'Operating wallet', currency: 'RUB', status: 'ACTIVE',
  available_minor: 100000, reserved_minor: 0, posted_minor: 100000, owner_id: 'user-1',
}
const manual = { id: 'manual-hold', account_id: 'account-1', amount_minor: 29, status: 'ACTIVE', created_at: '2026-11-25T00:00:00Z', reference_id: 'manual:user:key' }
const payment = { ...manual, id: 'payment-hold', amount_minor: 10000, reference_id: 'payment:payment-1' }

beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value(this: HTMLDialogElement) { this.setAttribute('open', '') },
  })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value(this: HTMLDialogElement) { this.removeAttribute('open') },
  })
})

beforeEach(() => {
  requestMock.mockReset()
  mutateMock.mockReset()
  notifyMock.mockReset()
  requestMock.mockResolvedValue({ items: [manual, payment], total: 2, page: 1, page_size: 100 })
})

function show(canReserve = true, canRelease = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><WalletHolds wallet={wallet} canReserve={canReserve} canRelease={canRelease} close={vi.fn()} /></QueryClientProvider>)
}

describe('wallet reservation controls', () => {
  it('loads real reservations and only allows manual holds to be released', async () => {
    show()
    expect(await screen.findByText('Payment reservation')).toBeInTheDocument()
    expect(requestMock).toHaveBeenCalledWith('/wallets/wallet-1/holds?page_size=100')
    expect(screen.getAllByRole('button', { name: /^Release reservation / })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Release reservation payment-hold' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Operating wallet reservations' })).toBeInTheDocument()
  })

  it('converts the entered RUB amount exactly before requesting a reservation', async () => {
    show()
    const amount = screen.getByRole('textbox', { name: /Reserve amount/ })
    fireEvent.change(amount, { target: { value: '0.29' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reserve funds' }))
    expect(mutateMock).toHaveBeenCalledWith('/wallets/wallet-1/holds', { amount_minor: 29 })
    await screen.findByText('Payment reservation')
  })

  it('requires explicit confirmation before releasing the selected reservation', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Release reservation manual-hold' }))
    expect(mutateMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm release' }))
    expect(mutateMock).toHaveBeenCalledWith('/holds/manual-hold/release', {})
  })

  it('keeps readonly wallets inspectable without offering new reservations', async () => {
    show(false, false)
    expect(await screen.findByText('Payment reservation')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reserve funds' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Release reservation / })).not.toBeInTheDocument()
  })

  it('shows a failed load without pretending the wallet has no reservations', async () => {
    requestMock.mockRejectedValue(new Error('Reservation service unavailable'))
    show()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Reservation service unavailable'))
    expect(screen.queryByText('No reservations yet')).not.toBeInTheDocument()
  })
})
