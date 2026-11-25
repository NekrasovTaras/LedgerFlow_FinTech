import { useEffect, useId, useRef, type ReactNode } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, Check, Inbox, LoaderCircle, Search, X } from 'lucide-react'
import { errorMessage } from '../app/context'
import { label } from '../features/money'

export function Spinner({ message = 'Loading live data…' }: { message?: string }) { return <div className="state-block" role="status"><LoaderCircle className="spin" size={22} /><span>{message}</span></div> }
export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) { return <div className="state-block error-state" role="alert"><AlertCircle size={24} /><strong>Unable to load data</strong><p>{errorMessage(error)}</p>{retry && <button className="button secondary" onClick={retry}>Try again</button>}</div> }
export function EmptyState({ title = 'No records yet', description = 'New activity will appear here when you use the sandbox.', action }: { title?: string; description?: string; action?: ReactNode }) { return <div className="state-block empty-state"><Inbox size={28} /><strong>{title}</strong><p>{description}</p>{action}</div> }
export function PageTitle({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) { return <div className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{description}</p></div>{actions && <div className="heading-actions">{actions}</div>}</div> }
export function Panel({ title, description, children, actions, className = '' }: { title?: string; description?: string; children: ReactNode; actions?: ReactNode; className?: string }) { return <section className={`panel ${className}`}>{title && <div className="panel-heading"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{actions}</div>}{children}</section> }
export function Status({ value }: { value: string | boolean | null | undefined }) {
  const text = typeof value === 'boolean' ? value ? 'AVAILABLE' : 'UNAVAILABLE' : value ?? 'UNKNOWN'
  const color = /^(SUCCEEDED|ACTIVE|ALLOW|APPROVED|COMPLETED|SUCCESS|AVAILABLE|HEALTHY|OK|CLOSED|DELIVERED|PUBLISHED|PASSED)$/i.test(text) ? 'green' : /^(FAILED|DECLINED|DECLINE|BLOCKED|UNAVAILABLE|OPEN|ERROR|DEAD|DEAD_LETTER|FAIL)$/i.test(text) ? 'red' : /^(UNKNOWN|REQUIRES_REVIEW|PENDING|PROCESSING|RUNNING|RETRY|AUTHORIZED)$/i.test(text) ? 'amber' : 'neutral'
  return <span className={`status status-${color}`}><i />{label(text)}</span>
}
export function SearchInput({ value, onChange, placeholder = 'Search records…' }: { value: string; onChange: (value: string) => void; placeholder?: string }) { return <label className="search-input"><Search size={16} /><span className="sr-only">{placeholder}</span><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label> }
export function Pagination({ page, total, pageSize = 20, onChange }: { page: number; total: number; pageSize?: number; onChange: (page: number) => void }) { const last = Math.max(1, Math.ceil(total / pageSize)); return <div className="pagination"><span>{total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}` : '0 records'}</span><div><button aria-label="Previous page" disabled={page <= 1} onClick={() => onChange(page - 1)}><ArrowLeft size={16} /></button><span>Page {page} of {last}</span><button aria-label="Next page" disabled={page >= last} onClick={() => onChange(page + 1)}><ArrowRight size={16} /></button></div></div> }
export function Field({ label: name, hint, children }: { label: string; hint?: string; children: ReactNode }) { return <label className="field"><span>{name}</span>{children}{hint && <small>{hint}</small>}</label> }
export function SubmitButton({ pending, children, disabled = false }: { pending: boolean; children: ReactNode; disabled?: boolean }) { return <button type="submit" className="button primary" disabled={pending || disabled}>{pending ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{pending ? 'Submitting…' : children}</button> }
export function Modal({ title, children, close, description }: { title: string; children: ReactNode; close: () => void; description?: string }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  useEffect(() => { const current = dialog.current; current?.showModal(); return () => { current?.close() } }, [])
  return <dialog ref={dialog} className="modal" aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined} onCancel={close} onClick={(event) => { if (event.target === event.currentTarget) close() }}><div className="modal-heading"><div><h2 id={titleId}>{title}</h2>{description && <p id={descriptionId}>{description}</p>}</div><button className="icon-button" aria-label="Close dialog" onClick={close}><X size={20} /></button></div>{children}</dialog>
}
export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre> }
