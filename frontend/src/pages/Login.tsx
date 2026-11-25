import { useState, type FormEvent } from 'react'
import { ArrowRight, CheckCircle2, GitBranch, Layers, ShieldCheck } from 'lucide-react'
import { useAuth, errorMessage } from '../app/context'
import { request } from '../api/client'

export function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('admin@ledgerflow.local')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [register, setRegister] = useState(false)
  const [name, setName] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError('')
    try {
      if (register) await request('/auth/register', { method: 'POST', body: JSON.stringify({ email, password, display_name: name }) }, false)
      await login(email, password)
    } catch (failure) { setError(errorMessage(failure)) } finally { setPending(false) }
  }
  return <div className="login-page"><section className="login-story"><div className="brand login-brand"><span className="brand-mark"><span /><span /></span><span>LedgerFlow<small>FINANCIAL INFRASTRUCTURE</small></span></div><div className="login-story-content"><p className="eyebrow">THE OPERATIONS WORKSPACE</p><h1>Every movement.<br />Accounted for.</h1><p>Explore the infrastructure behind a payment. From the first request to the final balanced posting.</p><div className="login-features"><span><Layers size={19} />Immutable, double-entry ledger</span><span><GitBranch size={19} />Traceable payment lifecycles</span><span><ShieldCheck size={19} />Controlled failure experiments</span></div></div><small className="login-disclaimer">Production-inspired architecture. Entirely virtual funds.</small></section><section className="login-form-area"><div className="login-form-card"><span className="environment-tag"><span />LOCAL SANDBOX</span><h2>{register ? 'Create your account' : 'Welcome to LedgerFlow'}</h2><p>{register ? 'Open a customer account in the local financial sandbox.' : 'Sign in to your financial operations workspace.'}</p><form onSubmit={(event) => { void submit(event) }}>{register && <label className="field"><span>Display name</span><input required maxLength={100} autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} /></label>}<label className="field"><span>Email address</span><input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label><label className="field"><span>Password</span><input type="password" autoComplete={register ? 'new-password' : 'current-password'} minLength={register ? 12 : undefined} required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" /></label>{error && <div className="inline-error" role="alert">{error}</div>}<button type="submit" className="button primary" disabled={pending}>{pending ? 'Opening workspace…' : register ? 'Create customer account' : 'Sign in to workspace'}<ArrowRight size={17} /></button></form><button className="auth-switch" onClick={() => { setRegister(!register); setError('') }}>{register ? 'Already have an account? Sign in' : 'New to the sandbox? Create an account'}</button><details className="demo-credentials"><summary>Sandbox demo accounts</summary><p>Demo accounts exist only when sandbox mode is explicitly enabled.</p><label className="field"><span>Choose a role</span><select value={email} onChange={(event) => setEmail(event.target.value)}>{['admin', 'customer', 'merchant', 'analyst', 'operator'].map((role) => <option value={`${role}@ledgerflow.local`} key={role}>{role}@ledgerflow.local</option>)}</select></label><p>Demo password: <code>DemoPass!2026</code></p><button type="button" className="button secondary" onClick={() => setPassword('DemoPass!2026')}>Use demo password</button></details><div className="login-safe"><CheckCircle2 size={15} /><span>No real bank accounts. No external payment services.</span></div></div></section></div>
}
