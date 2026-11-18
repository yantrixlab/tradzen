import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { Badge, Card, ErrorBox, PageTitle, Spinner } from '../components/ui';

export interface Feature { key: string; label: string; group: string; desc: string; kind: 'toggle' | 'limit'; min_role: 'trader' | 'editor' | 'admin'; default: boolean | number | null; enforced: boolean; trader_soon: boolean; locked: boolean; unit: string }
export interface Plan { id: number; name: string; description: string; features: Record<string, boolean | number | null>; is_default: boolean; users: number }
interface Catalog { features: Feature[]; plans: Plan[] }

const GROUPS = ['Menus', 'Signals & alerts', 'Strategies', 'Limits'];
const ROLE_LABEL = { trader: 'Everyone', editor: 'Editors and up', admin: 'Admin only' } as const;

/** One plan's value for one feature: a switch, or a number (empty = no limit, 0 = off). */
function Cell({ f, plan, onSet }: { f: Feature; plan: Plan; onSet: (value: boolean | number | null) => void }) {
  const own = f.key in plan.features;
  const value = own ? plan.features[f.key] : f.default;
  if (f.locked) return <span className="note">always on</span>;
  if (f.min_role === 'admin') return <span className="note">admin only</span>;
  if (f.kind === 'toggle') {
    return (
      <label className="inline-flex cursor-pointer items-center gap-2" title={own ? 'set in this plan' : 'the feature\'s default'}>
        <input type="checkbox" className="h-4 w-4 accent-[#8b5cf6]" checked={Boolean(value)} onChange={(e) => onSet(e.target.checked)} aria-label={`${f.label} in ${plan.name}`} />
        {!own && <span className="note">default</span>}
      </label>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <input key={`${plan.id}-${String(value)}`} type="number" min={0} max={100000} step={1} className="input !w-20" defaultValue={value === null ? '' : String(value)} placeholder="∞"
        aria-label={`${f.label} in ${plan.name}`}
        onBlur={(e) => { const t = e.target.value.trim(); const n = t === '' ? null : Math.floor(Number(t)); if (n !== value && (n === null || Number.isFinite(n))) onSet(n); }} />
      <span className="note">{value === null ? 'no limit' : f.unit}</span>
    </span>
  );
}

export default function Features() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['features'], queryFn: () => api<Catalog>('/api/features') });
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [copyOf, setCopyOf] = useState<number | ''>('');
  const done = () => { setErr(null); qc.invalidateQueries({ queryKey: ['features'] }); qc.invalidateQueries({ queryKey: ['users'] }); };
  const fail = (e: Error) => { setErr(e.message); qc.invalidateQueries({ queryKey: ['features'] }); };
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api<Plan>(`/api/plans/${id}`, { method: 'PUT', body }), onSuccess: done, onError: fail });
  const create = useMutation({ mutationFn: () => api<Plan>('/api/plans', { body: { name: name.trim(), copy_of: copyOf === '' ? null : copyOf } }), onSuccess: () => { setName(''); setCopyOf(''); done(); }, onError: fail });
  const remove = useMutation({ mutationFn: (id: number) => api(`/api/plans/${id}`, { method: 'DELETE' }), onSuccess: done, onError: fail });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const { features, plans } = q.data;
  function submit(e: FormEvent) { e.preventDefault(); create.mutate(); }

  return (
    <div>
      <PageTitle title="Features" sub="Which menus and capabilities each plan includes. The server enforces these on every request; hiding a menu is only the visible half." />
      {err && <div role="alert" className="mb-3 flex items-start justify-between gap-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down"><span>{err}</span><button className="text-xs underline" onClick={() => setErr(null)}>dismiss</button></div>}

      <Card title="Plans">
        <p className="note mb-3">A plan is a named set of switches and limits. New users get the <b className="text-fg">default plan</b>; the admin can assign another plan, set an end date (after it the user falls back to the default plan) or override single features for one user on the Users page. Paid plans plug in here later.</p>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <div className="min-w-[200px] flex-1"><label className="label" htmlFor="pn">New plan</label><input id="pn" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Pro" maxLength={40} /></div>
          <div><label className="label" htmlFor="pc">Start from</label>
            <select id="pc" className="input" value={copyOf} onChange={(e) => setCopyOf(e.target.value === '' ? '' : Number(e.target.value))}><option value="">defaults</option>{plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
          <button className="btn btn-primary" disabled={create.isPending || name.trim().length < 2}>Add plan</button>
        </form>
      </Card>

      <Card className="mt-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th min-w-[190px] bg-panel max-md:sticky max-md:left-0 max-md:z-[1] md:min-w-[280px]">Feature</th><th className="th">Available to</th>
                {plans.map((p) => (
                  <th key={p.id} className="th min-w-[150px] align-top normal-case tracking-normal">
                    <div className="flex items-center gap-1.5 text-sm font-semibold text-fg">{p.name}{p.is_default && <Badge tone="acc">DEFAULT</Badge>}</div>
                    <div className="note font-normal">{p.users} user{p.users === 1 ? '' : 's'}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {!p.is_default && <button className="btn btn-sm" onClick={() => patch.mutate({ id: p.id, body: { is_default: true } })}>Make default</button>}
                      <button className="btn btn-sm" onClick={() => { const n = prompt('Rename plan', p.name); if (n && n.trim() !== p.name) patch.mutate({ id: p.id, body: { name: n.trim() } }); }}>Rename</button>
                      {!p.is_default && <button className="btn btn-sm btn-danger" onClick={() => { if (confirm(`Delete “${p.name}”? Its ${p.users} user(s) move to the default plan.`)) remove.mutate(p.id); }}>Delete</button>}
                    </div>
                  </th>))}
              </tr>
            </thead>
            <tbody>
              {GROUPS.map((g) => {
                const rows = features.filter((f) => f.group === g);
                if (!rows.length) return null;
                return [
                  <tr key={`g-${g}`}><td colSpan={2 + plans.length} className="bg-panel2 px-2 py-1.5 text-[11px] font-bold uppercase tracking-wider text-mut">{g}</td></tr>,
                  ...rows.map((f) => (
                    <tr key={f.key}>
                      <td className="td bg-panel max-md:sticky max-md:left-0 max-md:z-[1] max-md:border-r max-md:border-r-line">
                        <div className="font-medium">{f.label} {!f.enforced && <Badge tone="warn" title="Stored now; it starts to work for traders when the data is per-user / strategies can be published (a later phase).">NOT ACTIVE YET</Badge>}</div>
                        <div className="note">{f.desc}</div>
                      </td>
                      <td className="td whitespace-nowrap text-mut">{ROLE_LABEL[f.min_role]}{f.trader_soon && f.min_role !== 'trader' && <div className="note">traders: later</div>}</td>
                      {plans.map((p) => (
                        <td key={p.id} className="td"><Cell f={f} plan={p} onSet={(value) => patch.mutate({ id: p.id, body: { features: { [f.key]: value } } })} /></td>))}
                    </tr>)),
                ];
              })}
            </tbody>
          </table>
        </div>
        <p className="note mt-3">“Available to” is the lowest role that can ever have the feature: a plan cannot give a Trader a staff feature, only promoting the role does. Admins always have everything.</p>
      </Card>
    </div>
  );
}
