import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner } from '../components/ui';
import { useCheckout } from '../components/CheckoutHost';
import { STATUS_TONE, daysLeft, money, type Catalog, type Order } from '../lib/pay';

const kindTone = (label: string) => (/usdt|crypto|btc|eth/i.test(label) ? 'bg-amber-500/15 text-amber-300' : /upi|inr|bank/i.test(label) ? 'bg-sky-500/15 text-sky-300' : 'bg-slate-500/20 text-slate-300');

/** Buy a plan: the plan cards show what each costs with each way of paying; "Get ..." opens the checkout window (components/CheckoutModal.tsx), where the person pays outside the app
 *  (UPI, USDT ...) and sends the payment's reference. The admin checks it and switches the plan on. */
export default function Plans() {
  const qc = useQueryClient();
  const cat = useQuery({ queryKey: ['pay-plans'], queryFn: () => api<Catalog>('/api/pay/plans') });
  const orders = useQuery({ queryKey: ['pay-orders'], queryFn: () => api<Order[]>('/api/pay/orders'), refetchInterval: 15000 });
  const { open } = useCheckout();

  const plans = cat.data?.plans ?? [];
  const pending = (orders.data ?? []).find((o) => o.status === 'pending') ?? null;
  const approved = (orders.data ?? []).filter((o) => o.status === 'approved').length;
  const lastApproved = useRef<number | null>(null);
  useEffect(() => {                                              // a payment the admin approved changes the plan: refresh who I am
    if (lastApproved.current !== null && approved > lastApproved.current) { qc.invalidateQueries({ queryKey: ['me'] }); qc.invalidateQueries({ queryKey: ['pay-plans'] }); }
    lastApproved.current = approved;
  }, [approved, qc]);
  const cancel = useMutation({ mutationFn: (id: number) => api(`/api/pay/orders/${id}/cancel`, { method: 'POST' }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['pay-orders'] }); } });

  if (cat.isError) return <ErrorBox error={cat.error} />;
  if (!cat.data) return <Spinner />;
  const cur = cat.data.current;
  const left = daysLeft(cur.until);
  const methodOf = (id: number) => cat.data!.methods.find((m) => m.id === id);

  return (
    <>
      <PageTitle title="Plans" sub="Pick a plan, pay with UPI or crypto, and send us the payment's reference. Your plan is switched on once the payment has been checked." />

      {/* your plan now */}
      <section className="relative mb-6 overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-r from-[#1b2540] via-[#10172e] to-[#0a0f20] p-5 sm:p-6">
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-amber-400/10 blur-3xl" />
        <div className="relative flex flex-wrap items-center gap-4">
          <div className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-amber-300 to-orange-500 text-white shadow-[0_10px_28px_rgba(249,115,22,.4),inset_0_1px_0_rgba(255,255,255,.5)]">
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="currentColor" aria-hidden="true"><path d="M3 8l4.5 4L12 5l4.5 7L21 8l-2 10H5L3 8zm2.6 12h12.8v2H5.6z" /></svg></div>
          <div className="min-w-0 flex-1">
            <div className="text-[10px] font-bold uppercase tracking-[.14em] text-slate-400">Your plan now</div>
            <div className="flex flex-wrap items-center gap-2"><span className="text-2xl font-extrabold tracking-tight text-white">{cur.name}</span>{cur.paid ? <Badge tone="up">PAID</Badge> : <Badge>FREE</Badge>}</div>
            <div className="mt-0.5 text-sm text-slate-400">{cur.paid && cur.until ? `Active until ${dateTime(cur.until)} · ${left} day${left === 1 ? '' : 's'} left. Paying again adds the days to the end of this period.` : 'Not a paid plan. Pick one below to unlock more.'}</div>
            {cur.paid && left !== null && <div className="mt-3 h-1.5 max-w-sm overflow-hidden rounded-full bg-white/10" role="presentation"><div className="h-full rounded-full bg-gradient-to-r from-amber-300 to-orange-500" style={{ width: `${Math.max(4, Math.min(100, (left / 30) * 100))}%` }} /></div>}
          </div>
          {pending && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3.5 py-2.5 text-sm text-amber-200">A payment of <b>{money(pending.amount, pending.currency)}</b> is waiting to be checked ({ago(pending.created)}).</div>}
        </div>
      </section>

      {/* the plans */}
      <h2 className="mb-3 text-sm font-bold uppercase tracking-[.14em] text-slate-400">Choose a plan</h2>
      {plans.length === 0 ? <Card><Empty>No plans are on sale yet. Ask the administrator.</Empty></Card> : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {plans.map((p) => {
            const yours = cur.plan_id === p.id && cur.paid;
            return (
              <section key={p.id} className={`group relative flex flex-col overflow-hidden rounded-[22px] border bg-gradient-to-b from-[#151d38] to-[#0a1020] p-5 transition hover:-translate-y-0.5 hover:shadow-[0_18px_50px_rgba(6,182,212,.12)] ${yours ? 'border-emerald-400/50' : 'border-white/10 hover:border-cyan-400/50'}`}>
                <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-white/[.05] to-transparent" />
                <div className="relative flex items-start justify-between gap-2">
                  <h3 className="text-xl font-extrabold tracking-tight text-white">{p.name}</h3>
                  {yours && <Badge tone="up">YOURS</Badge>}
                </div>
                {p.description && <p className="relative mt-1 text-sm text-slate-400">{p.description}</p>}
                <div className="relative mt-4 grid gap-2.5">
                  {p.prices.map((x) => {
                    const m = methodOf(x.method_id);
                    return m ? (
                      <div key={x.method_id} className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[.03] px-4 py-3">
                        <span className={`rounded-lg px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider ${kindTone(m.label + ' ' + m.kind)}`}>{m.label}</span>
                        <span className="text-right"><span className="text-2xl font-extrabold tabular-nums tracking-tight text-white">{money(x.amount, m.currency).replace(' USDT', '')}</span><span className="ml-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-500">/ {x.days} days</span></span>
                      </div>) : null;
                  })}
                </div>
                <button type="button" onClick={() => open(p.id)}
                  className="relative mt-5 inline-flex min-h-[48px] w-full items-center justify-center rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-600 px-5 text-sm font-extrabold uppercase tracking-wider text-white shadow-[0_12px_30px_rgba(14,116,230,.35),inset_0_1px_0_rgba(255,255,255,.3)] transition hover:brightness-110">
                  {yours ? 'Renew' : 'Get'} {p.name}</button>
              </section>);
          })}
        </div>)}

      <Card title="My payments" className="mt-6">
        {!orders.data ? <Spinner /> : orders.data.length === 0 ? <Empty>No payments yet.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Sent', 'Plan', 'Method', 'Amount', 'Reference', 'Status', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{orders.data.map((o) => (
              <tr key={o.id}>
                <td className="td note whitespace-nowrap" data-label="Sent">{dateTime(o.created)}</td><td className="td font-medium" data-label="Plan">{o.plan}</td><td className="td" data-label="Method">{o.method}{o.network ? ` · ${o.network}` : ''}</td>
                <td className="td tabular-nums" data-label="Amount">{money(o.amount, o.currency)}</td><td className="td font-mono text-xs" data-label="Reference"><span className="break-all">{o.reference}</span></td>
                <td className="td" data-label="Status"><Badge tone={STATUS_TONE[o.status]}>{o.status.toUpperCase()}</Badge>
                  {o.status === 'approved' && o.plan_until && <div className="note mt-1">until {dateTime(o.plan_until)}</div>}
                  {o.status === 'rejected' && o.review_note && <div className="mt-1 text-xs text-down">{o.review_note}</div>}</td>
                <td className="td stack-full">{o.status === 'pending' && <button className="btn btn-sm" disabled={cancel.isPending} onClick={() => window.confirm('Cancel this payment? You can send it again afterwards.') && cancel.mutate(o.id)}>Cancel</button>}</td>
              </tr>))}</tbody></table></div>)}
        {cancel.isError && <div className="mt-2"><ErrorBox error={cancel.error} /></div>}
        <p className="note mt-3">Your plan is switched on by a person who checks the payment against the account it was sent to, so it is not instant. Keep the payment's receipt until the plan is active.</p>
      </Card>
    </>
  );
}
