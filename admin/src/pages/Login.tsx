import { FormEvent, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../api';

/** The sign-in page of a hosted server. Accounts are created by the admin only; there is no sign-up. */
export default function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api('/api/auth/login', { body: { username: username.trim(), password } });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' });          // nothing of a previous user stays in the cache
      await qc.invalidateQueries({ queryKey: ['me'] });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'sign-in failed');
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full place-items-center overflow-y-auto bg-bg p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6" aria-labelledby="login-title">
        <div className="flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-acc to-acc2 text-xl">⚡</div>
          <div>
            <h1 id="login-title" className="text-lg font-bold leading-tight">Tradzen</h1>
            <p className="note">Sign in to continue</p>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="u">Username</label>
          <input id="u" className="input" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div>
          <label className="label" htmlFor="p">Password</label>
          <input id="p" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <div role="alert" className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{error}</div>}
        <button className="btn btn-primary w-full py-2" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="note text-center">No account yet? Accounts are created by the administrator.</p>
      </form>
    </div>
  );
}
