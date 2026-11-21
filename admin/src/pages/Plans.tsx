import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, PageTitle, Spinner } from '../components/ui';
import { NETWORKS, STATUS_TONE, daysLeft, money, readImage, refProblem, type Catalog, type Order, type PayMethod } from '../lib/pay';

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" className="btn btn-sm" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* clipboard blocked */ } }}>{done ? 'Copied' : label}</button>;
}

/** Buy a plan: pick the plan and how you pay, pay outside the app (UPI / USDT ...), then enter the payment's reference (a UPI UTR or a transaction hash). The admin checks it and switches the plan on. */
export default function Plans() {
  const qc = useQueryClient();
  const cat = useQuery({ queryKey: ['pay-plans'], queryFn: () => api<Catalog>('/api/pay/plans') });
  const orders = useQuery({ queryKey: ['pay-orders'], queryFn: () => api<Order[]>('/api/pay/orders'), refetchInterval: 15000 });
  const [planId, setPlanId] = useState<number | null>(null);
  const [methodId, setMethodId] = useState<number | null>(null);
  const [network, setNetwork] = useState<string>('TRC20');
  const [ref, setRef] = useState('');
  const [note, setNote] = useState('');
  const [proof, setProof] = useState<string | null>(null);
  const [proofName, setProofName] = useState('');
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);
  const form = useRef<HTMLDivElement>(null);

  const plans = cat.data?.plans ?? [];
  const plan = plans.find((p) => p.id === planId) ?? null;
  const methods: PayMethod[] = useMemo(() => (cat.data && plan ? cat.data.methods.filter((m) => plan.prices.some((p) => p.method_id === m.id)) : []), [cat.data, plan]);
  const method = methods.find((m) => m.id === methodId) ?? methods[0] ?? null;
  const price = plan && method ? plan.prices.find((p) => p.method_id === method.id) ?? null : null;
  const pending = (orders.data ?? []).find((o) => o.status === 'pending') ?? null;
  const approved = (orders.data ?? []).filter((o) => o.status === 'approved').length;
  const lastApproved = useRef<number | null>(null);
  useEffect(() => {                                              // a payment the admin approved changes the plan: refresh who I am
    if (lastApproved.current !== null && approved > lastApproved.current) { qc.invalidateQueries({ queryKey: ['me'] }); qc.invalidateQueries({ queryKey: ['pay-plans'] }); }
    lastApproved.current = approved;
  }, [approved, qc]);

  const problem = method ? refProblem(method.kind, method.kind === 'usdt' ? network : null, ref) : null;
  const submit = useMutation({
    mutationFn: () => api<Order>('/api/pay/orders', { body: { plan_id: plan!.id, method_id: method!.id, reference: ref, network: method!.kind === 'usdt' ? network : null, note, proof } }),
    onSuccess: () => {
      setSent('Submitted. Your plan switches on as soon as the payment has been checked.');
      setRef(''); setNote(''); setProof(null); setProofName(''); setTouched(false);
      qc.invalidateQueries({ queryKey: ['pay-orders'] });
    },
  });
  const cancel = useMutation({ mutationFn: (id: number) => api(`/api/pay/orders/${id}/cancel`, { method: 'POST' }), onSuccess: () => { setSent(null); qc.invalidateQueries({ queryKey: ['pay-orders'] }); } });

  if (cat.isError) return <ErrorBox error={cat.error} />;
  if (!cat.data) return <Spinner />;
  const cur = cat.data.current;
  const left = daysLeft(cur.until);

  const choose = (id: number) => {
    setPlanId(id); setMethodId(null); setSent(null); setTouched(false);
    setTimeout(() => form.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
  };

  return (
    <>
      <PageTitle title="Plans" sub="Pay for a plan with UPI or crypto, send us the payment's reference, and your plan is switched on once the payment has been checked." />

      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-mut">Your plan now</div>
            <div className="text-xl font-extrabold">{cur.name} {cur.paid ? <Badge tone="up">PAID</Badge> : <Badge>FREE</Badge>}</div>
            <div className="note">{cur.paid && cur.until ? `Active until ${dateTime(cur.until)} · ${left} day${left === 1 ? '' : 's'} left. Paying again adds the days to the end of this period.` : 'Not a paid plan. Pick one below to unlock more.'}</div>
          </div>
          {pending && <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">A payment of {money(pending.amount, pending.currency)} is waiting for review ({ago(pending.created)}).</div>}
        </div>
      </Card>

      {plans.length === 0 ? <Card><Empty>No plans are on sale yet. Ask the administrator.</Empty></Card> : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {plans.map((p) => (
            <section key={p.id} className={`card flex flex-col ${planId === p.id ? '!border-acc' : ''}`}>
              <div className="flex items-start justify-between gap-2"><h2 className="text-lg font-bold">{p.name}</h2>{cur.plan_id === p.id && cur.paid && <Badge tone="up">YOURS</Badge>}</div>
              {p.description && <p className="mt-1 text-sm text-mut">{p.description}</p>}
              <ul className="mt-3 space-y-1 text-sm">{p.prices.map((x) => { const m = cat.data!.methods.find((mm) => mm.id === x.method_id); return m ? <li key={x.method_id} className="flex justify-between gap-2"><span className="text-mut">{m.label}</span><b className="tabular-nums">{money(x.amount, m.currency)} <span className="font-normal text-mut">/ {x.days} days</span></b></li> : null; })}</ul>
              <button className="btn btn-primary mt-4" onClick={() => choose(p.id)}>{cur.plan_id === p.id && cur.paid ? 'Renew' : 'Buy'} {p.name}</button>
            </section>))}
        </div>)}

      {plan && method && price && (
        <div ref={form} className="scroll-mt-4">
          <Card title={`Pay for ${plan.name}`} className="mt-4">
            {methods.length > 1 && (
              <div className="mb-4 flex flex-wrap gap-1" role="group" aria-label="Payment method">
                {methods.map((m) => <button key={m.id} className={`btn btn-sm ${method.id === m.id ? 'btn-primary' : ''}`} aria-pressed={method.id === m.id} onClick={() => { setMethodId(m.id); setTouched(false); setSent(null); }}>{m.label}</button>)}
              </div>)}
            <div className="grid gap-5 lg:grid-cols-[minmax(0,260px)_1fr]">
              <div>
                {method.qr ? (
                  <button type="button" className="block w-full max-w-[260px] rounded-xl bg-white p-2" onClick={() => setZoom(!zoom)} title="Click to enlarge" aria-label="Payment QR code, click to enlarge">
                    <img src={`/api/pay/qr/${method.qr}`} alt={`${method.label} QR code`} className={`mx-auto ${zoom ? 'w-[min(80vw,420px)]' : 'w-full'}`} />
                  </button>) : <div className="grid h-40 place-items-center rounded-xl border border-dashed border-line text-sm text-mut">No QR code for this method</div>}
                <div className="mt-3 text-sm">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-mut">Amount to pay</div>
                  <div className="text-2xl font-extrabold tabular-nums">{money(price.amount, method.currency)}</div>
                  <div className="note">for {price.days} days of {plan.name}</div>
                </div>
                {method.address && <div className="mt-3 text-sm"><div className="text-[10px] font-semibold uppercase tracking-wider text-mut">{method.kind === 'upi' ? 'UPI ID' : 'Address'}</div><div className="break-all font-mono text-xs">{method.address}</div><div className="mt-1"><CopyButton text={method.address} /></div></div>}
              </div>

              <div>
                {method.instructions && <div className="mb-4 whitespace-pre-line rounded-lg border border-line bg-panel2/60 p-3 text-sm">{method.instructions}</div>}
                <ol className="mb-4 list-decimal space-y-1 pl-5 text-sm text-mut"><li>Pay exactly <b className="text-fg">{money(price.amount, method.currency)}</b> using the QR code or the {method.kind === 'upi' ? 'UPI ID' : 'address'} above.</li>
                  <li>Copy the payment's reference: {method.kind === 'upi' ? 'the 12-digit UTR / reference number from your UPI app' : 'the transaction hash (TXID) from your wallet or exchange'}.</li>
                  <li>Paste it below and send it. Each reference can be used once.</li></ol>
                {sent && <div className="mb-3 rounded-lg border border-up/40 bg-up/10 p-3 text-sm text-up">{sent}</div>}
                {pending ? <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">You already have a payment waiting for review. You can send another one after it has been checked, or cancel it in the list below.</div> : (
                  <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); setTouched(true); setSent(null); if (!problem) submit.mutate(); }}>
                    {method.kind === 'usdt' && <Field label="Network"><select className="input" value={network} onChange={(e) => setNetwork(e.target.value)}>{NETWORKS.map((n) => <option key={n} value={n}>{n}</option>)}</select></Field>}
                    <Field label={method.kind === 'upi' ? 'UTR / reference number' : 'Transaction hash (TXID)'} hint={touched && problem ? undefined : method.kind === 'upi' ? '12 digits, shown in your UPI app after the payment' : 'Copy it from the wallet or the exchange withdrawal details'}>
                      <input className="input font-mono" value={ref} onChange={(e) => setRef(e.target.value)} onBlur={() => setTouched(true)} autoComplete="off" spellCheck={false} inputMode={method.kind === 'upi' ? 'numeric' : 'text'} aria-invalid={touched && !!problem} />
                      {touched && problem && <span className="mt-1 block text-xs text-down">{problem}</span>}
                    </Field>
                    <Field label="Screenshot of the payment (optional)" hint="PNG, JPEG or WEBP, up to 1 MB. It helps the check go faster.">
                      <input className="input" type="file" accept="image/png,image/jpeg,image/webp" onChange={async (e) => {
                        const f = e.target.files?.[0]; setFileErr(null);
                        if (!f) { setProof(null); setProofName(''); return; }
                        const r = await readImage(f, 1_000_000);
                        if ('error' in r) { setFileErr(r.error); setProof(null); setProofName(''); e.target.value = ''; } else { setProof(r.url); setProofName(f.name); }
                      }} />
                      {fileErr && <span className="mt-1 block text-xs text-down">{fileErr}</span>}{proofName && <span className="note mt-1 block">{proofName}</span>}
                    </Field>
                    <Field label="Note (optional)"><input className="input" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Anything that helps us find the payment" /></Field>
                    {submit.isError && <ErrorBox error={submit.error} />}
                    <button className="btn btn-primary" type="submit" disabled={submit.isPending || !!fileErr}>{submit.isPending ? 'Sending…' : 'Send the payment reference'}</button>
                  </form>)}
              </div>
            </div>
          </Card>
        </div>)}

      <Card title="My payments" className="mt-4">
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
