import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, qs } from '../api';
import { ago, dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, PageTitle, Spinner, Stat, Tabs } from '../components/ui';
import { STATUS_TONE, money, readImage, type Order, type PayMethod } from '../lib/pay';
import { PairMultiSelect } from '../components/PairMultiSelect';

interface Stats { pending: number; month: { currency: string; orders: number; amount: number }[]; all_time: { currency: string; orders: number; amount: number }[] }
interface Overview { methods: PayMethod[]; prices: { plan_id: number; method_id: number; amount: number; days: number }[]; stats: Stats; plans: { id: number; name: string }[] }
interface OrdersResp { orders: Order[]; stats: Stats }

const sums = (rows: Stats['month']) => (rows.length ? rows.map((r) => money(r.amount, r.currency)).join(' · ') : '–');

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" className="btn btn-sm" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* blocked */ } }}>{done ? 'Copied' : 'Copy'}</button>;
}

/** One order waiting (or already decided): who, what, the reference to check against the bank / wallet, the proof, and the two decisions. */
function OrderCard({ o, onDone }: { o: Order; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const approve = useMutation({ mutationFn: () => api<Order>(`/api/pay-admin/orders/${o.id}/approve`, { body: {} }), onSuccess: onDone });
  const reject = useMutation({ mutationFn: () => api<Order>(`/api/pay-admin/orders/${o.id}/reject`, { body: { reason } }), onSuccess: () => { setRejecting(false); onDone(); } });
  const pending = o.status === 'pending';
  return (
    <div className="rounded-xl border border-line bg-panel2/50 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><b>{o.username}</b><Badge tone={STATUS_TONE[o.status]}>{o.status.toUpperCase()}</Badge><span className="note">{dateTime(o.created)} · {ago(o.created)}</span></div>
          <div className="mt-1 text-sm">{o.plan} · {o.days} days · via {o.method}{o.network ? ` (${o.network})` : ''}</div>
          <div className="mt-1 text-xl font-extrabold tabular-nums">{money(o.amount, o.currency)}</div>
          <div className="mt-2 flex flex-wrap items-center gap-2"><span className="text-[10px] font-semibold uppercase tracking-wider text-mut">{o.kind === 'upi' ? 'UTR' : 'Reference'}</span><code className="break-all text-xs">{o.reference}</code><Copy text={o.reference} /></div>
          {o.note && <div className="mt-1 text-sm text-mut">“{o.note}”</div>}
          {!pending && <div className="note mt-1">{o.status === 'approved' && o.plan_until ? `Plan until ${dateTime(o.plan_until)} · ` : ''}{o.reviewer ? `by ${o.reviewer}` : ''}{o.reviewed ? ` · ${dateTime(o.reviewed)}` : ''}{o.review_note ? ` · “${o.review_note}”` : ''}</div>}
        </div>
        {o.proof && <a href={`/api/pay-admin/proof/${o.proof}`} target="_blank" rel="noreferrer" title="Open the screenshot"><img src={`/api/pay-admin/proof/${o.proof}`} alt="Payment screenshot" className="h-24 w-24 rounded-lg border border-line object-cover" /></a>}
      </div>
      {pending && (
        <div className="mt-3 border-t border-line/70 pt-3">
          <p className="note mb-2">Check that this reference is in your bank statement / wallet, for this amount, and was not used for anyone else, before approving.</p>
          {rejecting ? (
            <div className="flex flex-wrap items-center gap-2">
              <input className="input !w-auto min-w-[16rem] flex-1" placeholder="Reason shown to the user" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} autoFocus />
              <button className="btn btn-sm !border-down/60 !text-down" disabled={!reason.trim() || reject.isPending} onClick={() => reject.mutate()}>Reject</button>
              <button className="btn btn-sm" onClick={() => setRejecting(false)}>Back</button>
            </div>) : (
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" disabled={approve.isPending} onClick={() => window.confirm(`Approve ${money(o.amount, o.currency)} from ${o.username}?\n\nThis switches the ${o.plan} plan on for ${o.days} days.`) && approve.mutate()}>{approve.isPending ? 'Approving…' : `Approve · ${o.days} days of ${o.plan}`}</button>
              <button className="btn !text-down" onClick={() => setRejecting(true)}>Reject…</button>
            </div>)}
          {(approve.isError || reject.isError) && <div className="mt-2"><ErrorBox error={approve.error ?? reject.error} /></div>}
        </div>)}
    </div>
  );
}

/** A payment method: its details, its QR image and on / off. */
function MethodEditor({ m, onDone }: { m: PayMethod | null; onDone: () => void }) {
  const [f, setF] = useState({ kind: m?.kind ?? 'upi', label: m?.label ?? '', currency: m?.currency ?? 'INR', address: m?.address ?? '', instructions: m?.instructions ?? '', active: m?.active ?? true, sort: m?.sort ?? 0 });
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => (m ? api<PayMethod>(`/api/pay-admin/methods/${m.id}`, { method: 'PUT', body: f }) : api<PayMethod>('/api/pay-admin/methods', { body: f })), onSuccess: () => { setErr(null); onDone(); }, onError: (e: Error) => setErr(e.message) });
  const del = useMutation({ mutationFn: () => api<{ result: string }>(`/api/pay-admin/methods/${m!.id}`, { method: 'DELETE' }), onSuccess: onDone, onError: (e: Error) => setErr(e.message) });
  const qr = useMutation({ mutationFn: (image: string | null) => api<PayMethod>(`/api/pay-admin/methods/${m!.id}/qr`, { method: 'PUT', body: { image } }), onSuccess: () => { setErr(null); onDone(); }, onError: (e: Error) => setErr(e.message) });
  return (
    <div className="rounded-xl border border-line bg-panel2/50 p-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Type"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as PayMethod['kind'] })}><option value="upi">UPI (UTR reference)</option><option value="usdt">USDT / crypto (transaction hash)</option><option value="other">Other</option></select></Field>
        <Field label="Name shown to users"><input className="input" value={f.label} maxLength={60} onChange={(e) => setF({ ...f, label: e.target.value })} placeholder="UPI" /></Field>
        <Field label="Currency" hint="INR, USDT, USD …"><input className="input" value={f.currency} maxLength={5} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Order in the list"><input className="input" type="number" value={f.sort} onChange={(e) => setF({ ...f, sort: Number(e.target.value) || 0 })} /></Field>
        <div className="sm:col-span-2"><Field label={f.kind === 'upi' ? 'UPI ID' : 'Wallet address'}><input className="input font-mono" value={f.address} maxLength={200} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field></div>
        <div className="sm:col-span-2"><Field label="Instructions shown to the user"><textarea className="input" rows={2} value={f.instructions} maxLength={1500} onChange={(e) => setF({ ...f, instructions: e.target.value })} placeholder="e.g. Send the exact amount, then paste the UTR below." /></Field></div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> On sale</label>
        <button className="btn btn-primary btn-sm" disabled={save.isPending || !f.label.trim()} onClick={() => save.mutate()}>{m ? 'Save' : 'Add the method'}</button>
        {m && <button className="btn btn-sm !text-down" disabled={del.isPending} onClick={() => window.confirm(`Remove "${m.label}"? A method that has orders is only switched off.`) && del.mutate()}>Remove</button>}
        {!m && <button className="btn btn-sm" onClick={onDone}>Cancel</button>}
      </div>
      {m && (
        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line/70 pt-3">
          {m.qr ? <img src={`/api/pay/qr/${m.qr}`} alt="QR code" className="h-24 w-24 rounded-lg bg-white p-1" /> : <div className="grid h-24 w-24 place-items-center rounded-lg border border-dashed border-line text-center text-xs text-mut">no QR yet</div>}
          <div className="space-y-2">
            <label className="btn btn-sm cursor-pointer">{m.qr ? 'Replace the QR image' : 'Upload the QR image'}
              <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={async (e) => {
                const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
                const r = await readImage(file, 500_000);
                if ('error' in r) setErr(r.error); else qr.mutate(r.url);
              }} /></label>
            {m.qr && <button className="btn btn-sm !text-down" onClick={() => qr.mutate(null)}>Remove the QR</button>}
            <div className="note">PNG, JPEG or WEBP, up to 500 KB. Users see it next to the amount.</div>
          </div>
        </div>)}
      {err && <div className="mt-2"><ErrorBox error={err} /></div>}
    </div>
  );
}

/** What each plan costs with each method, and for how many days. An empty amount = not sold with that method. */
function PriceGrid({ o, onDone }: { o: Overview; onDone: () => void }) {
  const [draft, setDraft] = useState<Record<string, { amount: string; days: string }>>({});
  const [err, setErr] = useState<string | null>(null);
  const key = (p: number, m: number) => `${p}:${m}`;
  const cur = (p: number, m: number) => o.prices.find((x) => x.plan_id === p && x.method_id === m);
  const save = useMutation({
    mutationFn: ({ p, m }: { p: number; m: number }) => { const d = draft[key(p, m)]; return api('/api/pay-admin/prices', { method: 'PUT', body: { plan_id: p, method_id: m, amount: d.amount === '' ? null : Number(d.amount), days: Number(d.days) || 30 } }); },
    onSuccess: (_r, v) => { setErr(null); setDraft((d) => { const n = { ...d }; delete n[key(v.p, v.m)]; return n; }); onDone(); }, onError: (e: Error) => setErr(e.message),
  });
  if (!o.methods.length || !o.plans.length) return <Empty>Add a payment method first{!o.plans.length ? ' (and a plan on the Features page)' : ''}.</Empty>;
  return (
    <div className="overflow-x-auto"><table className="w-full">
      <thead><tr><th className="th">Plan</th>{o.methods.map((m) => <th key={m.id} className="th">{m.label} ({m.currency}){m.active === false ? ' · off' : ''}</th>)}</tr></thead>
      <tbody>{o.plans.map((p) => (
        <tr key={p.id}><td className="td font-medium">{p.name}</td>
          {o.methods.map((m) => {
            const c = cur(p.id, m.id), d = draft[key(p.id, m.id)];
            const amount = d ? d.amount : c ? String(c.amount) : '', days = d ? d.days : c ? String(c.days) : '30';
            return (
              <td key={m.id} className="td"><div className="flex items-center gap-1">
                <input className="input !w-24" inputMode="decimal" placeholder="not sold" aria-label={`${p.name} price in ${m.currency}`} value={amount} onChange={(e) => setDraft({ ...draft, [key(p.id, m.id)]: { amount: e.target.value, days } })} />
                <span className="note">/</span>
                <input className="input !w-16" inputMode="numeric" aria-label={`${p.name} days`} value={days} onChange={(e) => setDraft({ ...draft, [key(p.id, m.id)]: { amount, days: e.target.value } })} /><span className="note">d</span>
                {d && <button className="btn btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate({ p: p.id, m: m.id })}>Save</button>}
              </div></td>);
          })}</tr>))}</tbody></table>
      {err && <div className="mt-2"><ErrorBox error={err} /></div>}
    </div>
  );
}


interface Platform { lock: boolean; free_pairs: string[]; signup_open: boolean; pairs: { symbol: string; name: string; market: string | null }[] }

/** The chart platform's switches: lock markets behind a paid plan (and which ones stay free) and let visitors create their own account. */
function PlatformSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['platform-admin'], queryFn: () => api<Platform>('/api/platform-admin/settings') });
  const [draft, setDraft] = useState<{ lock: boolean; free: string[]; signup: boolean } | null>(null);
  const save = useMutation({
    mutationFn: (d: { lock: boolean; free: string[]; signup: boolean }) => api('/api/platform-admin/settings', { method: 'PUT', body: { lock: d.lock, free_pairs: d.free, signup_open: d.signup } }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['platform-admin'] }); qc.invalidateQueries({ queryKey: ['markets'] }); qc.invalidateQueries({ queryKey: ['auth-config'] }); },
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const cur = draft ?? { lock: q.data.lock, free: q.data.free_pairs, signup: q.data.signup_open };
  const set = (p: Partial<typeof cur>) => setDraft({ ...cur, ...p });
  return (
    <div className="space-y-4">
      <Card title="Markets: free and paid">
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={cur.lock} onChange={(e) => set({ lock: e.target.checked })} />
          <span><b>Lock markets behind a paid plan.</b> While this is off, every signed-in user opens every market (how it works today). When it is on, a user without a plan that has <i>Open every pair</i> (Features page) opens only the free markets below. Admins and editors always open everything.</span></label>
        {cur.lock && (
          <div className="mt-3 max-w-md">
            <div className="label">Free markets</div>
            <PairMultiSelect unit="markets" options={q.data.pairs.map((p) => ({ symbol: p.symbol, n: 0, payout: null }))} value={cur.free} onChange={(v) => set({ free: v })} />
            <p className="note mt-1">{cur.free.length === 0 ? 'No free market: everything is locked for accounts without a paid plan.' : `${cur.free.length} free market${cur.free.length === 1 ? '' : 's'}.`}</p>
          </div>)}
        <p className="note mt-3">The lock is applied on the server to the market list, the live chart and every other place that returns a pair's candles, so it cannot be bypassed from the page. Create a paid plan on the Features page, switch on <i>Open every pair</i> in it, give it a price under Methods &amp; prices.</p>
      </Card>
      <Card title="Sign-up">
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={cur.signup} onChange={(e) => set({ signup: e.target.checked })} />
          <span><b>Let visitors create their own account.</b> A "Create account" tab appears on the sign-in page. New accounts are traders on the default plan, never staff. At most 5 sign-ups an hour per address and 40 an hour in total; every one is in the audit log. There is no e-mail check, so it does not stop one person making several accounts.</span></label>
      </Card>
      {save.isError && <ErrorBox error={save.error} />}
      <div className="flex items-center gap-2"><button className="btn btn-primary" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>{save.isPending ? 'Saving…' : 'Save'}</button>{draft && <button className="btn" onClick={() => setDraft(null)}>Discard</button>}</div>
    </div>
  );
}

/** The admin's side of plan payments: review what users sent, keep the payment methods and the prices. */
export default function Payments() {
  const qc = useQueryClient();
  const [tab, setTab] = useState('review');
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [adding, setAdding] = useState(false);
  const pendingQ = useQuery({ queryKey: ['pay-admin-orders', 'pending'], queryFn: () => api<OrdersResp>('/api/pay-admin/orders?status=pending'), refetchInterval: 15000 });
  const allQ = useQuery({ queryKey: ['pay-admin-orders', 'all', status, q], queryFn: () => api<OrdersResp>(`/api/pay-admin/orders${qs({ status, q })}`), enabled: tab === 'all' });
  const over = useQuery({ queryKey: ['pay-admin-overview'], queryFn: () => api<Overview>('/api/pay-admin/overview') });
  const refresh = () => { qc.invalidateQueries({ queryKey: ['pay-admin-orders'] }); qc.invalidateQueries({ queryKey: ['pay-admin-overview'] }); qc.invalidateQueries({ queryKey: ['pay-plans'] }); };
  if (pendingQ.isError) return <ErrorBox error={pendingQ.error} />;
  if (!pendingQ.data || !over.data) return <Spinner />;
  const st = pendingQ.data.stats, o = over.data;
  const pending = pendingQ.data.orders;

  return (
    <>
      <PageTitle title="Payments" sub="Users pay outside the app and send the payment's reference. Check it against your bank or wallet, then approve: the plan switches on for the paid days." />
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="Waiting for review" value={st.pending} tone={st.pending ? 'warn' : undefined} sub="oldest first below" />
        <Stat label="Approved this month" value={sums(st.month)} sub={st.month.map((r) => `${r.orders} order${r.orders === 1 ? '' : 's'}`).join(' · ') || 'none yet'} />
        <Stat label="Approved in total" value={sums(st.all_time)} sub={st.all_time.map((r) => `${r.orders} order${r.orders === 1 ? '' : 's'}`).join(' · ') || 'none yet'} />
      </div>
      <Tabs tabs={[{ key: 'review', label: `To review (${st.pending})` }, { key: 'all', label: 'All orders' }, { key: 'setup', label: 'Methods & prices' }, { key: 'platform', label: 'Markets & sign-up' }]} value={tab} onChange={setTab} />

      {tab === 'review' && (pending.length === 0 ? <Card><Empty>Nothing is waiting for review.</Empty></Card> : <div className="space-y-3">{pending.map((x) => <OrderCard key={x.id} o={x} onDone={refresh} />)}</div>)}

      {tab === 'all' && (
        <Card title="All orders" right={<div className="flex flex-wrap items-center gap-2"><select className="input !w-auto" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option>{['pending', 'approved', 'rejected', 'cancelled'].map((s) => <option key={s} value={s}>{s}</option>)}</select>
          <input className="input !w-56" placeholder="Search user or reference…" value={q} onChange={(e) => setQ(e.target.value)} /></div>}>
          {!allQ.data ? <Spinner /> : allQ.data.orders.length === 0 ? <Empty>No orders match.</Empty> : <div className="space-y-3">{allQ.data.orders.map((x) => <OrderCard key={x.id} o={x} onDone={refresh} />)}</div>}
        </Card>)}

      {tab === 'platform' && <PlatformSettings />}

      {tab === 'setup' && (
        <div className="space-y-4">
          <Card title="Payment methods" right={<button className="btn btn-sm" onClick={() => setAdding(!adding)}>{adding ? 'Close' : '+ Add a method'}</button>}>
            <div className="space-y-3">
              {adding && <MethodEditor m={null} onDone={() => { setAdding(false); refresh(); }} />}
              {o.methods.length === 0 && !adding ? <Empty>No payment methods yet. Add UPI and / or USDT, upload your QR image, then set prices below.</Empty> : o.methods.map((m) => <MethodEditor key={`${m.id}-${m.qr ?? ''}-${m.label}-${m.active}`} m={m} onDone={refresh} />)}
            </div>
          </Card>
          <Card title="Prices (what each plan costs, per method)"><PriceGrid o={o} onDone={refresh} /><p className="note mt-3">Plans themselves (what they unlock) are made on the Features page. A plan appears for sale once it has a price with at least one method that is on.</p></Card>
        </div>)}
    </>
  );
}
