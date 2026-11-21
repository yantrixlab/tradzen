import { FormEvent, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { useLast } from '../lib/useLast';
import { AcceptTerms, LegalSections, useLegal } from '../components/LegalText';

interface PublicPlans { plans: { id: number; name: string; description: string; prices: { method: string; currency: string; amount: number; days: number }[] }[] }
const price = (p: { currency: string; amount: number }) => `${p.currency === 'INR' ? '₹' : p.currency === 'USD' || p.currency === 'USDT' ? '$' : ''}${p.amount % 1 ? p.amount.toFixed(2) : p.amount}${p.currency === 'USDT' ? ' USDT' : p.currency === 'INR' || p.currency === 'USD' ? '' : ` ${p.currency}`}`;

/** The sign-in page of a hosted server. Accounts are made by the admin, or by the visitor when the admin has opened sign-up (then a "Create account" tab appears). The plans on sale are shown below. */
export default function Login() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ['auth-config'], queryFn: () => api<{ signup_open: boolean }>('/api/auth/config'), retry: false, staleTime: 60000 });
  const plans = useQuery({ queryKey: ['public-plans'], queryFn: () => api<PublicPlans>('/api/public/plans'), retry: false, staleTime: 60000 });
  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [accept, setAccept] = useState(false);
  const [showLegal, setShowLegal] = useState(false);
  const legal = useLegal();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // the app clears its query cache whenever any request comes back 401 (a session that ended): keep what was last known so the form does not change under the visitor's hands
  const cfgSeen = useLast(cfg.data ?? null), plansSeen = useLast(plans.data ?? null);
  const signup = !!cfgSeen?.signup_open;
  const up = signup && mode === 'up';

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (up && password !== again) { setError('The two passwords are not the same.'); return; }
    if (up && !accept) { setError('Tick the box to accept the terms, the risk warning and the refund policy.'); return; }
    setBusy(true);
    setError(null);
    try {
      await api(up ? '/api/auth/register' : '/api/auth/login', { body: up ? { username: username.trim(), password, accept_terms: accept } : { username: username.trim(), password } });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });          // nothing of a previous user stays in the cache
      await qc.invalidateQueries({ queryKey: ['me'] });
    } catch (err) {
      setError(err instanceof Error ? err.message : up ? 'sign-up failed' : 'sign-in failed');
      setPassword(''); setAgain('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full place-items-center overflow-y-auto bg-bg p-4">
      <div className="w-full max-w-sm space-y-4 py-4">
        <form onSubmit={submit} className="card space-y-4 p-6" aria-labelledby="login-title">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-acc to-acc2 text-xl">⚡</div>
            <div>
              <h1 id="login-title" className="text-lg font-bold leading-tight">Tradzen</h1>
              <p className="note">{up ? 'Create your account' : 'Sign in to continue'}</p>
            </div>
          </div>
          {signup && (
            <div className="grid grid-cols-2 gap-1 rounded-lg border border-line p-1 text-sm font-semibold" role="tablist">
              {([['in', 'Sign in'], ['up', 'Create account']] as const).map(([k, l]) => (
                <button key={k} type="button" role="tab" aria-selected={mode === k} onClick={() => { setMode(k); setError(null); }} className={`rounded-md py-1.5 ${mode === k ? 'bg-acc text-white' : 'text-mut hover:text-fg'}`}>{l}</button>))}
            </div>)}
          <div>
            <label className="label" htmlFor="u">Username</label>
            <input id="u" className="input" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
            {up && <span className="note mt-1 block">3-40 letters, digits and _ . @ -</span>}
          </div>
          <div>
            <label className="label" htmlFor="p">Password</label>
            <input id="p" className="input" type="password" autoComplete={up ? 'new-password' : 'current-password'} value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {up && (
            <div>
              <label className="label" htmlFor="p2">Repeat the password</label>
              <input id="p2" className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
              <span className="note mt-1 block">At least 10 characters, nothing common.</span>
            </div>)}
          {up && <AcceptTerms id="acc" checked={accept} onChange={setAccept} />}
          {error && <div role="alert" className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{error}</div>}
          <button className="btn btn-primary w-full py-2" disabled={busy || !username || !password || (up && (!again || !accept))}>{busy ? (up ? 'Creating…' : 'Signing in…') : up ? 'Create account' : 'Sign in'}</button>
          {!signup && <p className="note text-center">No account yet? Accounts are created by the administrator.</p>}
        </form>

        {legal && (
          <div className="text-center"><button type="button" className="note underline" aria-expanded={showLegal} onClick={() => setShowLegal(!showLegal)}>{showLegal ? 'Hide' : 'Read'} the terms of use, risk warning and refund policy</button>{showLegal && <div className="text-left"><LegalSections doc={legal} /></div>}</div>)}
        {plansSeen && plansSeen.plans.length > 0 && (
          <section className="card space-y-3 p-5" aria-label="Plans">
            <h2 className="text-sm font-bold">Plans</h2>
            {plansSeen.plans.map((p) => (
              <div key={p.id} className="rounded-lg border border-line bg-panel2/50 p-3">
                <div className="font-semibold">{p.name}</div>
                {p.description && <div className="note">{p.description}</div>}
                <ul className="mt-1 space-y-0.5 text-sm">{p.prices.map((x, i) => <li key={i} className="flex justify-between gap-2"><span className="text-mut">{x.method}</span><b className="tabular-nums">{price(x)} <span className="font-normal text-mut">/ {x.days} days</span></b></li>)}</ul>
              </div>))}
            <p className="note">{signup ? 'Create an account, then open Plans to pay and unlock every market.' : 'Sign in, then open Plans to pay and unlock every market.'}</p>
          </section>)}
      </div>
    </div>
  );
}
