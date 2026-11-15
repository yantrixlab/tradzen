import { FormEvent, Fragment, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, dateTime } from '../format';
import { useMe } from '../hooks';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner } from '../components/ui';
import type { Feature, Plan } from './Features';

type Role = 'admin' | 'editor' | 'trader';
interface User { id: number; username: string; role: Role; plan_id: number | null; plan_until: number | null; active: boolean; must_change_pw: boolean; env_admin: boolean; created: number; last_login: number | null; sessions: number; tokens: number; qx_linked: boolean; qx_hint: string | null; qx_at: number | null }
interface Audit { id: number; ts: number; actor: string; action: string; target: string; detail: Record<string, unknown> }
interface Saved { user: User; generated_password: string | null }

const ROLE_INFO: Record<Role, [string, string]> = {
  admin: ['Admin', 'Everything: users, features, system settings.'],
  editor: ['Editor', 'Builds, tests and publishes strategies. No user or system management.'],
  trader: ['Trader', 'A general user: gets the signals and the features the plan allows.'],
};

type Val = boolean | number | null;
interface UserFeatures { overrides: Record<string, Val>; effective: Record<string, { value: Val; source: string }>; plan: string }
const RANK: Record<Role, number> = { trader: 0, editor: 1, admin: 2 };
const GROUPS = ['Menus', 'Signals & alerts', 'Strategies', 'Limits'];
const show = (f: Feature, v: Val) => (f.kind === 'toggle' ? (v ? 'on' : 'off') : v === null ? 'no limit' : `${v} ${f.unit}`);

/** The features of one user: what they get now (and why), and per-feature overrides that beat the plan. */
function Overrides({ user, features, onClose }: { user: User; features: Feature[]; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['user-features', user.id], queryFn: () => api<UserFeatures>(`/api/users/${user.id}/features`) });
  const [draft, setDraft] = useState<Record<string, Val | 'default'>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { if (q.data) setDraft({ ...q.data.overrides }); }, [q.data]);
  const save = useMutation({
    mutationFn: () => api(`/api/users/${user.id}/features`, { method: 'PUT', body: { overrides: draft } }),
    onSuccess: () => { setMsg({ ok: true, text: 'Saved.' }); qc.invalidateQueries({ queryKey: ['user-features', user.id] }); qc.invalidateQueries({ queryKey: ['audit'] }); },
    onError: (e: Error) => setMsg({ ok: false, text: e.message }),
  });
  if (!q.data) return <div className="p-3 text-mut">Loading…</div>;
  const rows = features.filter((f) => !f.locked && RANK[user.role] >= RANK[f.min_role] && user.role !== 'admin');
  return (
    <div className="rounded-lg border border-line bg-panel2 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">Features of <b>{user.username}</b> · plan <b>{q.data.plan}</b></div>
        <div className="flex items-center gap-2">{msg && <span role="status" className={`text-xs ${msg.ok ? 'text-up' : 'text-down'}`}>{msg.text}</span>}
          <button className="btn btn-sm btn-primary" onClick={() => { setMsg(null); save.mutate(); }} disabled={save.isPending}>Save overrides</button>
          <button className="btn btn-sm" onClick={onClose}>Close</button></div>
      </div>
      {!rows.length ? <p className="note">There is nothing to override for this role.</p> : (
        <div className="grid gap-x-6 gap-y-1 md:grid-cols-2">
          {GROUPS.flatMap((g) => rows.filter((f) => f.group === g)).map((f) => {
            const cur = q.data.effective[f.key];
            const d = draft[f.key] ?? 'default';
            return (
              <div key={f.key} className="flex items-center justify-between gap-2 border-t border-line py-1.5">
                <div className="min-w-0"><div className="truncate text-sm">{f.label}</div><div className="note">now: {show(f, cur.value)} · from {cur.source}</div></div>
                {f.kind === 'toggle' ? (
                  <select aria-label={`Override ${f.label}`} className="input !w-32" value={d === 'default' ? 'default' : d ? 'on' : 'off'}
                    onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === 'default' ? 'default' : e.target.value === 'on' })}>
                    <option value="default">Plan default</option><option value="on">Always on</option><option value="off">Always off</option></select>
                ) : (
                  <span className="inline-flex items-center gap-1.5">
                    <input type="number" min={0} className="input !w-20" aria-label={`Override ${f.label}`} placeholder="plan" disabled={d === null}
                      value={d === 'default' || d === null ? '' : String(d)} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === '' ? 'default' : Math.max(0, Math.floor(Number(e.target.value))) })} />
                    <label className="note flex items-center gap-1"><input type="checkbox" checked={d === null} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.checked ? null : 'default' })} />no limit</label>
                  </span>
                )}
              </div>);
          })}
        </div>
      )}
    </div>
  );
}

export default function Users() {
  const qc = useQueryClient();
  const me = useMe().data?.user;
  const cat = useQuery({ queryKey: ['features'], queryFn: () => api<{ features: Feature[]; plans: Plan[] }>('/api/features') });
  const [open, setOpen] = useState<number | null>(null);
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/api/users'), refetchInterval: 15000 });
  const audit = useQuery({ queryKey: ['audit'], queryFn: () => api<Audit[]>('/api/audit?n=40'), refetchInterval: 30000 });
  const [notice, setNotice] = useState<{ user: string; password: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['audit'] }); };
  const fail = (e: Error) => setErr(e.message);

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api<Saved>(`/api/users/${id}`, { method: 'PATCH', body }),
    onSuccess: (r) => { setErr(null); if (r.generated_password) setNotice({ user: r.user.username, password: r.generated_password }); refresh(); }, onError: fail,
  });
  const signOut = useMutation({ mutationFn: (id: number) => api(`/api/users/${id}/logout`, { method: 'POST', body: {} }), onSuccess: refresh, onError: fail });
  const remove = useMutation({ mutationFn: (id: number) => api(`/api/users/${id}`, { method: 'DELETE' }), onSuccess: refresh, onError: fail });
  const unlink = useMutation({ mutationFn: (id: number) => api(`/api/users/${id}/quotex`, { method: 'DELETE' }), onSuccess: refresh, onError: fail });

  const [name, setName] = useState('');
  const [role, setRole] = useState<Role>('trader');
  const [pw, setPw] = useState('');
  const create = useMutation({
    mutationFn: () => api<Saved>('/api/users', { body: { username: name.trim(), role, password: pw || null, must_change_pw: true } }),
    onSuccess: (r) => { setErr(null); setNotice({ user: r.user.username, password: r.generated_password ?? pw }); setName(''); setPw(''); refresh(); }, onError: fail,
  });
  function submit(e: FormEvent) { e.preventDefault(); create.mutate(); }

  if (users.isError) return <ErrorBox error={users.error} />;
  if (!users.data) return <Spinner />;

  return (
    <div>
      <PageTitle title="Users" sub="Accounts are created and managed here only. There is no public sign-up." />
      {err && <div role="alert" className="mb-3 flex items-start justify-between gap-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down"><span>{err}</span><button className="text-xs underline" onClick={() => setErr(null)}>dismiss</button></div>}
      {notice && (
        <div className="mb-3 rounded-lg border border-warn/40 bg-warn/10 p-3">
          <div className="text-sm font-semibold text-warn">Password for {notice.user}: give it to them now, it is not shown again.</div>
          <div className="mt-2 flex items-center gap-2"><code className="select-all rounded bg-panel2 px-2 py-1.5 text-sm">{notice.password}</code>
            <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(notice.password)}>Copy</button>
            <button className="btn btn-sm" onClick={() => setNotice(null)}>Done</button></div>
          <p className="note mt-1">They must choose their own password at the first sign-in.</p>
        </div>
      )}

      <Card title="Add a user">
        <form onSubmit={submit} className="grid gap-3 md:grid-cols-[1fr_160px_1fr_auto] md:items-end">
          <div><label className="label" htmlFor="nu">Username</label><input id="nu" className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" placeholder="3-40 letters, digits, _ . @ -" required /></div>
          <div><label className="label" htmlFor="nr">Role</label>
            <select id="nr" className="input" value={role} onChange={(e) => setRole(e.target.value as Role)}>{(Object.keys(ROLE_INFO) as Role[]).map((r) => <option key={r} value={r}>{ROLE_INFO[r][0]}</option>)}</select></div>
          <div><label className="label" htmlFor="npw">Password</label><input id="npw" className="input" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" placeholder="leave empty to generate one" /></div>
          <button className="btn btn-primary" disabled={create.isPending || name.trim().length < 3}>Create user</button>
        </form>
        <p className="note mt-2">{ROLE_INFO[role][1]}</p>
      </Card>

      <Card title={`Accounts (${users.data.length})`} className="mt-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr>{['User', 'Role', 'Plan', 'Status', 'Quotex account', 'Last sign-in', 'Sessions', 'Tokens', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{users.data.map((u) => {
              const self = u.id === me?.id;
              const locked = u.env_admin || self;
              return (
                <Fragment key={u.id}>
                <tr>
                  <td className="td whitespace-nowrap"><span className="font-medium">{u.username}</span>{u.env_admin && <span className="ml-2"><Badge tone="acc" title="Configured in the server's .env file">ENV</Badge></span>}{self && <span className="ml-2 note">(you)</span>}
                    {u.must_change_pw && <div className="note text-warn">must choose a password</div>}</td>
                  <td className="td">
                    <select aria-label={`Role of ${u.username}`} className="input !w-auto" value={u.role} disabled={locked || patch.isPending} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}>
                      {(Object.keys(ROLE_INFO) as Role[]).map((r) => <option key={r} value={r}>{ROLE_INFO[r][0]}</option>)}</select></td>
                  <td className="td">
                    {u.role === 'admin' ? <span className="note">everything</span> : (
                      <div className="flex flex-col gap-1">
                        <select aria-label={`Plan of ${u.username}`} className="input !w-auto" value={u.plan_id ?? ''} disabled={patch.isPending}
                          onChange={(e) => patch.mutate({ id: u.id, body: { plan_id: e.target.value === '' ? null : Number(e.target.value) } })}>
                          <option value="">{cat.data?.plans.find((p) => p.is_default)?.name ?? 'Default'} (default)</option>
                          {cat.data?.plans.filter((p) => !p.is_default).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
                        <label className="note flex items-center gap-1">until
                          <input type="date" aria-label={`Plan end date of ${u.username}`} className="input !w-auto !py-0.5 text-xs" value={u.plan_until ? new Date(u.plan_until * 1000).toISOString().slice(0, 10) : ''}
                            onChange={(e) => patch.mutate({ id: u.id, body: { plan_until: e.target.value ? new Date(`${e.target.value}T23:59:59`).getTime() / 1000 : null } })} /></label>
                      </div>)}</td>
                  <td className="td">{u.active ? <Badge tone="up">ACTIVE</Badge> : <Badge tone="down">OFF</Badge>}</td>
                  <td className="td whitespace-nowrap">
                    {u.qx_linked ? (
                      <span className="inline-flex items-center gap-2"><span className="tabular-nums" title={u.qx_at ? `linked ${dateTime(u.qx_at)}` : ''}>···{u.qx_hint || '?'}</span>
                        <button className="btn btn-sm" title="Lets this user link a different Quotex account the next time their extension connects" onClick={() => { if (confirm(`Unlink the Quotex account of ${u.username}? Their next connection links whichever Quotex account it is then logged into.`)) unlink.mutate(u.id); }}>Unlink</button></span>
                    ) : <span className="note">not linked yet</span>}</td>
                  <td className="td whitespace-nowrap text-mut" title={u.last_login ? dateTime(u.last_login) : ''}>{u.last_login ? ago(u.last_login) : 'never'}</td>
                  <td className="td tabular-nums text-mut">{u.sessions}</td><td className="td tabular-nums text-mut">{u.tokens}</td>
                  <td className="td whitespace-nowrap text-right">
                    <div className="inline-flex flex-wrap justify-end gap-1.5">
                      <button className="btn btn-sm" onClick={() => { if (confirm(`Reset the password of ${u.username}? They are signed out and get a new one.`)) patch.mutate({ id: u.id, body: { reset_password: true } }); }}>Reset password</button>
                      {u.role !== 'admin' && <button className="btn btn-sm" onClick={() => setOpen(open === u.id ? null : u.id)}>Features</button>}
                      <button className="btn btn-sm" onClick={() => signOut.mutate(u.id)} title="Ends every browser session and disconnects every extension of this user">Sign out everywhere</button>
                      <button className="btn btn-sm" disabled={locked} onClick={() => patch.mutate({ id: u.id, body: { active: !u.active } })}>{u.active ? 'Deactivate' : 'Activate'}</button>
                      <button className="btn btn-sm btn-danger" disabled={locked} onClick={() => { if (confirm(`Delete ${u.username}? This cannot be undone.`)) remove.mutate(u.id); }}>Delete</button>
                    </div></td>
                </tr>
                {open === u.id && cat.data && <tr><td className="td" colSpan={9}><Overrides user={u} features={cat.data.features} onClose={() => setOpen(null)} /></td></tr>}
                </Fragment>);
            })}</tbody>
          </table>
        </div>
        <p className="note mt-3">The admin from the server&apos;s .env file is always an admin and is changed there. Changing a role takes effect at the next request; deactivating or resetting signs the person out.</p>
      </Card>

      <Card title="Recent admin activity" className="mt-4">
        {!audit.data?.length ? <Empty>Nothing yet.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead><tr>{['When', 'Who', 'Action', 'Target', 'Detail'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{audit.data.map((a) => (
              <tr key={a.id}><td className="td whitespace-nowrap text-mut" title={dateTime(a.ts)}>{ago(a.ts)}</td><td className="td">{a.actor}</td>
                <td className="td"><Badge tone={a.action.includes('failed') ? 'down' : 'mut'}>{a.action}</Badge></td><td className="td">{a.target}</td>
                <td className="td note">{Object.entries(a.detail).map(([k, v]) => `${k}: ${String(v)}`).join(' · ')}</td></tr>))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
