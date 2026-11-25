import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownLeft, ArrowRight, LockKeyhole, Plus, Unlock, Wallet as WalletIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { request } from '../api/client'
import type { Journal, ListResponse, Wallet } from '../api/types'
import { useAction, useAuth, useToast, errorMessage } from '../app/context'
import { dateTime, money, parseRubles, shortId } from '../features/money'
import { EmptyState, ErrorState, Field, Modal, PageTitle, Panel, Spinner, Status, SubmitButton } from '../components/ui'
import { WalletHolds } from '../components/WalletHolds'

export function Wallets() {
  const [create, setCreate] = useState(false)
  const [selected, setSelected] = useState<Wallet | null>(null)
  const [history, setHistory] = useState<Wallet | null>(null)
  const [holds, setHolds] = useState<Wallet | null>(null)
  const [confirmation, setConfirmation] = useState<Wallet | null>(null)
  const { user } = useAuth()
  const canManage = ['administrator', 'operator'].includes(user?.role ?? '')
  const query = useQuery({ queryKey: ['wallets'], queryFn: () => request<ListResponse<Wallet>>('/wallets?page_size=100') })
  const action = useAction(`/wallets/${confirmation?.id}/${confirmation?.status === 'BLOCKED' ? 'unblock' : 'block'}`, 'Wallet status updated.', () => setConfirmation(null))
  return <><PageTitle title="Wallets" description="Manage virtual funds, available balances and payment reservations." actions={<button className="button primary" onClick={() => setCreate(true)}><Plus size={16} />Create wallet</button>} />{query.isLoading ? <Spinner /> : query.isError ? <ErrorState error={query.error} retry={() => { void query.refetch() }} /> : query.data?.items.length ? <div className="wallet-grid">{query.data.items.map((wallet) => <Panel key={wallet.id} className="wallet-card"><div className="wallet-card-top"><span className="wallet-emblem"><WalletIcon size={21} /></span><Status value={wallet.status} /></div><h2>{wallet.name}</h2><span className="wallet-id mono" title={wallet.id}>{wallet.id}<span>{wallet.currency}</span></span><p className="wallet-balance-label">Available balance</p><strong className="wallet-balance">{money(wallet.available_minor)}</strong><div className="wallet-balances"><div><span>Reserved</span><b>{money(wallet.reserved_minor)}</b></div><div><span>Posted balance</span><b>{money(wallet.posted_minor)}</b></div></div><div className="wallet-actions"><button className="button secondary" disabled={wallet.status === 'BLOCKED' || (wallet.owner_id !== user?.id && !canManage)} onClick={() => setSelected(wallet)}><ArrowDownLeft size={15} />Sandbox top-up</button><button className="button ghost" onClick={() => setHolds(wallet)}><LockKeyhole size={14} />Reservations</button><button className="button ghost" onClick={() => setHistory(wallet)}>History<ArrowRight size={14} /></button>{canManage && <button className="icon-button" title={wallet.status === 'BLOCKED' ? 'Unblock wallet' : 'Block wallet'} aria-label={`${wallet.status === 'BLOCKED' ? 'Unblock' : 'Block'} ${wallet.name}`} onClick={() => setConfirmation(wallet)}>{wallet.status === 'BLOCKED' ? <Unlock size={16} /> : <LockKeyhole size={16} />}</button>}</div></Panel>)}</div> : <Panel><EmptyState title="No wallets yet" description="Create your first RUB wallet to explore the financial sandbox." action={<button className="button primary" onClick={() => setCreate(true)}><Plus size={16} />Create wallet</button>} /></Panel>}<div className="info-note"><LockKeyhole size={16} /><span>Balances are backed by immutable double-entry postings. Sandbox top-ups create a journal entry through the system funding account.</span></div>{create && <WalletCreate close={() => setCreate(false)} />}{selected && <WalletTopup wallet={selected} close={() => setSelected(null)} />}{history && <WalletHistory wallet={history} close={() => setHistory(null)} />}{holds && <WalletHolds wallet={holds} canReserve={holds.status === 'ACTIVE' && (holds.owner_id === user?.id || canManage)} canRelease={holds.owner_id === user?.id || canManage} close={() => setHolds(null)} />}{confirmation && <Modal title={`${confirmation.status === 'BLOCKED' ? 'Unblock' : 'Block'} wallet?`} description={`${confirmation.name} · ${shortId(confirmation.id)}`} close={() => setConfirmation(null)}><p className="modal-copy">{confirmation.status === 'BLOCKED' ? 'This wallet will be available for financial operations again.' : 'New financial operations will be denied while this wallet is blocked.'}</p><div className="form-actions"><button className="button secondary" onClick={() => setConfirmation(null)}>Keep current status</button><button className="button danger" disabled={action.isPending} onClick={() => action.mutate({})}>{action.isPending ? 'Updating…' : 'Confirm status change'}</button></div></Modal>}</>
}
function WalletCreate({ close }: { close: () => void }) {
  const [name, setName] = useState('')
  const action = useAction('/wallets', 'Wallet created.', close)
  return <Modal title="Create wallet" description="A new virtual RUB wallet, backed by the ledger." close={close}><form onSubmit={(event) => { event.preventDefault(); action.mutate({ name, currency: 'RUB' }) }}><Field label="Wallet name"><input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Operating wallet" autoFocus /></Field><Field label="Currency"><input value="RUB — Russian ruble" disabled /></Field><div className="form-actions"><button type="button" className="button secondary" onClick={close}>Cancel</button><SubmitButton pending={action.isPending}>Create wallet</SubmitButton></div></form></Modal>
}
function WalletTopup({ wallet, close }: { wallet: Wallet; close: () => void }) {
  const [amount, setAmount] = useState('1000.00')
  const notify = useToast()
  const action = useAction(`/wallets/${wallet.id}/sandbox-topup`, 'Sandbox funds added through the ledger.', close)
  function submit(event: FormEvent) { event.preventDefault(); try { action.mutate({ amount_minor: parseRubles(amount) }) } catch (error) { notify(errorMessage(error), 'error') } }
  return <Modal title="Add sandbox funds" description={`Virtual top-up to ${wallet.name}. No real money is involved.`} close={close}><form onSubmit={submit}><Field label="Amount (RUB)" hint="Use at most two decimal places."><input inputMode="decimal" required value={amount} onChange={(event) => setAmount(event.target.value)} autoFocus /></Field><div className="form-actions"><button type="button" className="button secondary" onClick={close}>Cancel</button><SubmitButton pending={action.isPending}>Add virtual funds</SubmitButton></div></form></Modal>
}
function WalletHistory({ wallet, close }: { wallet: Wallet; close: () => void }) {
  const query = useQuery({ queryKey: ['wallet-history', wallet.id], queryFn: () => request<ListResponse<Journal>>(`/wallets/${wallet.id}/transactions?page_size=20`) })
  return <Modal title={`${wallet.name} history`} description="Journal entries from the actual ledger." close={close}>{query.isLoading ? <Spinner /> : query.isError ? <ErrorState error={query.error} /> : query.data?.items.length ? <div className="compact-history">{query.data.items.map((item) => <div key={item.id}><div><strong>{item.description || item.kind}</strong><small>{dateTime(item.created_at)}</small></div><span className="mono">{shortId(item.id)}</span></div>)}</div> : <EmptyState title="No ledger entries yet" />}<div className="form-actions"><Link className="button secondary" to="/ledger">Open ledger explorer<ArrowRight size={14} /></Link><button className="button primary" onClick={close}>Done</button></div></Modal>
}
