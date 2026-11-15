import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago } from '../format';
import { useMe } from '../hooks';
import { Badge, Card, Empty, PageTitle } from '../components/ui';

interface Token { id: number; label: string; feeder: boolean; created: number; last_used: number | null }

/** Change the own password. With `forced` (a new or reset account) it is the only thing the person can do until it is done. */
export function PasswordForm({ forced = false }: { forced?: boolean }) {
  const qc = useQueryClient();
  const [old, setOld] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: () => api('/api/auth/password', { body: { old, new: next } }),
    onSuccess: () => { setMsg({ ok: true, text: 'Password changed.' }); setOld(''); setNext(''); setAgain(''); qc.invalidateQueries({ queryKey: ['me'] }); },
    onError: (e: Error) => setMsg({ ok: false, text: e.message }),
  });
  const mismatch = again !== '' && again !== next;
  function submit(e: FormEvent) { e.preventDefault(); setMsg(null); if (!mismatch) save.mutate(); }
  return (
    <form onSubmit={submit} className="space-y-3">
      <div><label className="label" htmlFor="op">Current password</label><input id="op" type="password" autoComplete="current-password" className="input" value={old} onChange={(e) => setOld(e.target.value)} required /></div>
      <div><label className="label" htmlFor="np">New password</label><input id="np" type="password" autoComplete="new-password" className="input" value={next} onChange={(e) => setNext(e.target.value)} minLength={8} required /></div>
      <div><label className="label" htmlFor="rp">Repeat new password</label><input id="rp" type="password" autoComplete="new-password" className="input" value={again} onChange={(e) => setAgain(e.target.value)} required />
        {mismatch && <p className="mt-1 text-xs text-down">The two passwords differ.</p>}</div>
      {msg && <div role="status" className={`rounded-lg border px-3 py-2 text-sm ${msg.ok ? 'border-up/40 bg-up/10 text-up' : 'border-down/40 bg-down/10 text-down'}`}>{msg.text}</div>}
      <button className="btn btn-primary" disabled={save.isPending || !old || next.length < 8 || mismatch || !again}>{forced ? 'Choose my password' : 'Change password'}</button>
      <p className="note">At least 8 characters; nothing common like “password”.</p>
    </form>
  );
}

/** Link the Chrome extension to this account with a personal access token (shown once). */
function Tokens({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['tokens'], queryFn: () => api<Token[]>('/api/tokens') });
  const [label, setLabel] = useState('my browser');
  const [feeder, setFeeder] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const make = useMutation({
    mutationFn: () => api<{ secret: string }>('/api/tokens', { body: { label, feeder } }),
    onSuccess: (r) => { setFresh(r.secret); setCopied(false); qc.invalidateQueries({ queryKey: ['tokens'] }); },
  });
  const revoke = useMutation({ mutationFn: (id: number) => api(`/api/tokens/${id}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['tokens'] }) });
  return (
    <Card title="Chrome extension">
      <p className="note mb-3">In the extension popup, enter this server&apos;s address and an access token. Each browser needs its own token; revoke one here to disconnect it.</p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[180px] flex-1"><label className="label" htmlFor="tl">Name</label><input id="tl" className="input" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} /></div>
        {isAdmin && <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={feeder} onChange={(e) => setFeeder(e.target.checked)} />Price feed (the tab that streams Quotex prices)</label>}
        <button className="btn btn-primary" onClick={() => make.mutate()} disabled={make.isPending}>Create token</button>
      </div>
      {make.isError && <p className="mt-2 text-sm text-down">{(make.error as Error).message}</p>}
      {fresh && (
        <div className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-3">
          <div className="text-sm font-semibold text-warn">Copy it now: it is shown only once.</div>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 flex-1 select-all break-all rounded bg-panel2 px-2 py-1.5 text-xs">{fresh}</code>
            <button className="btn btn-sm" onClick={() => { navigator.clipboard?.writeText(fresh).then(() => setCopied(true)).catch(() => undefined); }}>{copied ? 'Copied' : 'Copy'}</button>
          </div>
        </div>
      )}
      <div className="mt-4 overflow-x-auto">
        {!list.data?.length ? <Empty>No token yet.</Empty> : (
          <table className="w-full text-sm">
            <thead><tr>{['Name', 'Type', 'Created', 'Last used', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{list.data.map((t) => (
              <tr key={t.id}>
                <td className="td font-medium">{t.label}</td>
                <td className="td">{t.feeder ? <Badge tone="acc">PRICE FEED</Badge> : <Badge>ALERTS</Badge>}</td>
                <td className="td text-mut">{ago(t.created)}</td><td className="td text-mut">{t.last_used ? ago(t.last_used) : 'never'}</td>
                <td className="td text-right"><button className="btn btn-sm btn-danger" onClick={() => { if (confirm(`Revoke “${t.label}”? The extension using it stops working.`)) revoke.mutate(t.id); }}>Revoke</button></td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

export default function Account() {
  const me = useMe().data;
  if (!me) return null;
  const u = me.user;
  return (
    <div>
      <PageTitle title="My account" sub={<>Signed in as <b className="text-fg">{u.username}</b> · role <b className="text-fg">{u.role}</b></>} />
      <div className="grid gap-4 lg:grid-cols-2">
        {me.auth_enabled ? <Card title="Password"><PasswordForm /></Card> : <Card title="Password"><p className="note">Sign-in is switched off on this local server, so there is no password to change.</p></Card>}
        {me.auth_enabled ? <Tokens isAdmin={u.role === 'admin'} /> : <Card title="Chrome extension"><p className="note">On this local server the extension needs no token.</p></Card>}
      </div>
    </div>
  );
}
