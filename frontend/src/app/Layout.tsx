import { Suspense, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowLeftRight, BookOpen, ChevronDown, CircleHelp, Command, CreditCard, FlaskConical, Gauge, LayoutDashboard, LogOut, Menu, Network, Search, Settings, ShieldCheck, Wallet, Webhook, X, type LucideIcon } from 'lucide-react'
import { useAuth, useToast, errorMessage } from './context'
import { request } from '../api/client'
import type { Health } from '../api/types'
import { label } from '../features/money'
import { Spinner } from '../components/ui'

interface NavigationItem { to: string; name: string; icon: LucideIcon; roles?: string[] }
interface NavigationGroup { name: string; items: NavigationItem[] }
const staffRoles = ['administrator', 'operator', 'risk_analyst']
const groups: NavigationGroup[] = [
  { name: 'WORKSPACE', items: [{ to: '/', name: 'Overview', icon: LayoutDashboard }, { to: '/wallets', name: 'Wallets', icon: Wallet }, { to: '/payments', name: 'Payments', icon: CreditCard }, { to: '/transfers', name: 'Transfers', icon: ArrowLeftRight }] },
  { name: 'OPERATIONS', items: [{ to: '/ledger', name: 'Ledger explorer', icon: BookOpen }, { to: '/risk', name: 'Risk center', icon: ShieldCheck, roles: staffRoles }, { to: '/providers', name: 'Providers', icon: Network, roles: staffRoles }, { to: '/webhooks', name: 'Webhooks', icon: Webhook }, { to: '/reconciliation', name: 'Reconciliation', icon: Gauge, roles: staffRoles }] },
  { name: 'DEVELOPMENT', items: [{ to: '/chaos', name: 'Chaos Lab', icon: FlaskConical, roles: ['administrator', 'operator'] }, { to: '/system', name: 'System health', icon: Activity, roles: staffRoles }] },
]
export function Layout() {
  const { user, logout } = useAuth()
  const notify = useToast()
  const [menuOpen, setMenuOpen] = useState(false)
  const [search, setSearch] = useState('')
  const navigate = useNavigate()
  const location = useLocation()
  const health = useQuery({ queryKey: ['health'], queryFn: () => request<Health>('/system/health'), refetchInterval: 30000 })
  const visibleGroups = groups.map((group) => ({ ...group, items: group.items.filter((item) => !item.roles || item.roles.includes(user?.role ?? '')) })).filter((group) => group.items.length > 0)
  const section = groups.flatMap((group) => group.items).find((item) => item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to))?.name ?? (location.pathname === '/settings' ? 'Settings' : 'Payment inspector')
  return <div className="app-shell">
    {menuOpen && <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
    <aside className={`sidebar ${menuOpen ? 'is-open' : ''}`}>
      <NavLink to="/" className="brand" onClick={() => setMenuOpen(false)}><span className="brand-mark"><span /><span /></span><span>Ledger<span className="brand-light">Flow</span><small>FINANCIAL INFRASTRUCTURE</small></span></NavLink>
      <div className="workspace-switcher"><div className="workspace-icon">LF</div><div><strong>LedgerFlow Sandbox</strong><small>Local development</small></div><ChevronDown size={15} /></div>
      <nav aria-label="Main navigation">{visibleGroups.map((group) => <div className="nav-group" key={group.name}><p>{group.name}</p>{group.items.map(({ to, name, icon: Icon }) => <NavLink to={to} key={to} end={to === '/'} onClick={() => setMenuOpen(false)}><Icon size={18} /><span>{name}</span>{name === 'Chaos Lab' && <span className="nav-new">LAB</span>}</NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><NavLink to="/settings" onClick={() => setMenuOpen(false)}><Settings size={18} />Settings</NavLink><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer"><CircleHelp size={18} />API documentation</a><div className="sandbox-note"><ShieldCheck size={16} /><span>Sandbox environment<small>Virtual funds. Local providers.</small></span></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={20} /> : <Menu size={20} />}</button><span>Workspace</span><span className="slash">/</span><strong>{section}</strong></div><div className="topbar-right"><form className="global-search" onSubmit={(event) => { event.preventDefault(); navigate(`/payments?search=${encodeURIComponent(search)}`) }}><Search size={15} /><input aria-label="Search payments" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search payments…" /><Command size={13} /></form><span className="environment-tag"><span />SANDBOX</span><div className="user-avatar" title={`${user?.display_name} · ${label(user?.role ?? '')}`}>{user?.display_name?.split(' ').map((part) => part[0]).slice(0, 2).join('') || 'LF'}</div><button className="icon-button logout" aria-label="Sign out" onClick={() => { void logout().catch((error: unknown) => notify(errorMessage(error), 'error')) }}><LogOut size={17} /></button></div></header>
      <main className="main-content"><Suspense fallback={<Spinner />}><Outlet /></Suspense></main>
      <footer className="app-footer"><span><span className={`dot ${health.data?.status === 'healthy' || health.data?.status === 'ok' ? 'green-dot' : 'amber-dot'}`} />{health.isError ? 'API connection unavailable' : health.data ? `System ${health.data.status}` : 'Checking system status…'}</span><span>Financial Infrastructure & Payment Processing Engine <span className="footer-divider">/</span> Virtual funds only</span></footer>
    </div>
  </div>
}
