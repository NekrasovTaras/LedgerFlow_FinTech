import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LockKeyhole, Unlock } from 'lucide-react'
import { request } from '../api/client'
import type { Hold, ListResponse, Wallet } from '../api/types'
import { errorMessage, useAction, useToast } from '../app/context'
import { dateTime, money, parseRubles, shortId } from '../features/money'
import { EmptyState, ErrorState, Field, Modal, Spinner, Status, SubmitButton } from './ui'

interface WalletHoldsProps {
  wallet: Wallet
  canReserve: boolean
  canRelease: boolean
  close: () => void
}

export function WalletHolds({ wallet, canReserve, canRelease, close }: WalletHoldsProps) {
  const [amount, setAmount] = useState('100.00')
  const [selected, setSelected] = useState<Hold | null>(null)
  const notify = useToast()
  const query = useQuery({
    queryKey: ['wallet-holds', wallet.id],
    queryFn: () => request<ListResponse<Hold>>(`/wallets/${wallet.id}/holds?page_size=100`),
  })
  const reserve = useAction<Hold>(
    `/wallets/${wallet.id}/holds`,
    'Funds reserved. The posted balance is unchanged.',
  )
  const release = useAction<Hold>(
    `/holds/${selected?.id}/release`,
    'Manual reservation released.',
    () => setSelected(null),
  )

  function submit(event: FormEvent) {
    event.preventDefault()
    try {
      reserve.mutate({ amount_minor: parseRubles(amount) })
    } catch (error) {
      notify(errorMessage(error), 'error')
    }
  }

  const busy = reserve.isPending || release.isPending
  return (
    <Modal
      title={`${wallet.name} reservations`}
      description="Active and historical reservations from the financial core. Payment reservations are managed by their payment."
      close={() => { if (!busy) close() }}
    >
      {canReserve && (
        <form onSubmit={submit}>
          <Field label="Reserve amount (RUB)" hint="A reservation reduces available funds without posting a charge.">
            <input
              inputMode="decimal"
              required
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              disabled={busy}
            />
          </Field>
          <div className="form-actions">
            <SubmitButton pending={reserve.isPending} disabled={busy && !reserve.isPending}>
              <LockKeyhole size={15} />Reserve funds
            </SubmitButton>
          </div>
        </form>
      )}
      {query.isLoading ? <Spinner message="Loading reservations…" /> : query.isError ? (
        <ErrorState error={query.error} retry={() => { void query.refetch() }} />
      ) : query.data?.items.length ? (
        <div className="table-scroll">
          <table>
            <thead><tr><th>Reservation</th><th>Amount</th><th>Status</th><th>Created</th><th /></tr></thead>
            <tbody>
              {query.data.items.map((hold) => {
                const manual = hold.reference_id.startsWith('manual:')
                return (
                  <tr key={hold.id}>
                    <td><strong>{manual ? 'Manual reservation' : 'Payment reservation'}</strong><small className="mono">{shortId(hold.id)}</small></td>
                    <td className="amount">{money(hold.amount_minor)}</td>
                    <td><Status value={hold.status} /></td>
                    <td>{dateTime(hold.created_at)}</td>
                    <td>{canRelease && manual && hold.status === 'ACTIVE' && (
                      <button
                        className="button secondary small"
                        disabled={busy}
                        onClick={() => setSelected(hold)}
                        aria-label={`Release reservation ${hold.id}`}
                      >
                        <Unlock size={14} />Release
                      </button>
                    )}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title="No reservations yet" description="Manual and payment reservations will appear here when created." />}
      {selected && (
        <div className="info-note" role="group" aria-label="Confirm reservation release">
          <Unlock size={17} />
          <div>
            <p>Release {money(selected.amount_minor)} from this manual reservation?</p>
            <div className="form-actions">
              <button className="button secondary" disabled={busy} onClick={() => setSelected(null)}>Keep reservation</button>
              <button className="button primary" disabled={busy} onClick={() => release.mutate({})}>
                {release.isPending ? 'Releasing…' : 'Confirm release'}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="form-actions"><button className="button secondary" disabled={busy} onClick={close}>Done</button></div>
    </Modal>
  )
}
