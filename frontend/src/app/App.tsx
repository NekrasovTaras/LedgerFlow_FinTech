import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, ToastProvider, useAuth } from './context'
import { Layout } from './Layout'
import { Login } from '../pages/Login'
import { Spinner } from '../components/ui'

const Overview = lazy(() => import('../pages/Overview').then((module) => ({ default: module.Overview })))
const Wallets = lazy(() => import('../pages/Wallets').then((module) => ({ default: module.Wallets })))
const Payments = lazy(() => import('../pages/Payments').then((module) => ({ default: module.Payments })))
const PaymentInspector = lazy(() => import('../pages/Payments').then((module) => ({ default: module.PaymentInspector })))
const Transfers = lazy(() => import('../pages/Transfers').then((module) => ({ default: module.Transfers })))
const Ledger = lazy(() => import('../pages/Ledger').then((module) => ({ default: module.Ledger })))
const Providers = lazy(() => import('../pages/Operations').then((module) => ({ default: module.Providers })))
const RiskCenter = lazy(() => import('../pages/Operations').then((module) => ({ default: module.RiskCenter })))
const Webhooks = lazy(() => import('../pages/Operations').then((module) => ({ default: module.Webhooks })))
const Reconciliation = lazy(() => import('../pages/Reconciliation').then((module) => ({ default: module.Reconciliation })))
const ChaosLab = lazy(() => import('../pages/Chaos').then((module) => ({ default: module.ChaosLab })))
const Settings = lazy(() => import('../pages/System').then((module) => ({ default: module.Settings })))
const SystemHealth = lazy(() => import('../pages/System').then((module) => ({ default: module.SystemHealth })))

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5000, refetchOnWindowFocus: false }, mutations: { retry: false } } })
function AuthenticatedApp() {
  const { user, loading } = useAuth()
  if (loading) return <div className="boot-screen"><Spinner message="Opening your workspace…" /></div>
  if (!user) return <Login />
  return <BrowserRouter><Routes><Route element={<Layout />}><Route index element={<Overview />} /><Route path="wallets" element={<Wallets />} /><Route path="payments" element={<Payments />} /><Route path="payments/:id" element={<PaymentInspector />} /><Route path="transfers" element={<Transfers />} /><Route path="ledger" element={<Ledger />} /><Route path="risk" element={<RiskCenter />} /><Route path="providers" element={<Providers />} /><Route path="webhooks" element={<Webhooks />} /><Route path="reconciliation" element={<Reconciliation />} /><Route path="chaos" element={<ChaosLab />} /><Route path="system" element={<SystemHealth />} /><Route path="settings" element={<Settings />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></BrowserRouter>
}
export function App() { return <QueryClientProvider client={client}><ToastProvider><AuthProvider><AuthenticatedApp /></AuthProvider></ToastProvider></QueryClientProvider> }
