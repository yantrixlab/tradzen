import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ErrorBox } from './ui';
import { AcceptTerms } from './LegalText';
import { NETWORKS, money, readImage, refProblem, type Catalog, type Order, type PayMethod, type PlanOffer } from '../lib/pay';

/** The checkout window: a two-panel dialog (what you get on the left, the payment on the right). Nothing is charged here: the person pays outside the app (UPI, USDT ...), sends the
 *  payment's reference, and a person checks it before the plan is switched on, so the wording says "Submit payment", not "Activate". */

const Crown = () => <svg viewBox="0 0 24 24" className="h-7 w-7" fill="currentColor" aria-hidden="true"><path d="M3 8l4.5 4L12 5l4.5 7L21 8l-2 10H5L3 8zm2.6 12h12.8v2H5.6z" /></svg>;
const Tick = ({ className = 'h-3.5 w-3.5' }: { className?: string }) => <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;
const Bolt = () => <svg viewBox="0 0 24 24" className="h-4 w-4 text-amber-400" fill="currentColor" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6z" /></svg>;
const Shield = () => <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3zm-1 14-4-4 1.4-1.4L11 13.2l4.6-4.6L17 10l-6 6z" /></svg>;

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-semibold text-slate-200 hover:bg-white/10" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* clipboard blocked */ } }}>{done ? 'Copied ✓' : 'Copy'}</button>;
}

const kindTone = (k: string) => (k === 'usdt' ? 'bg-amber-500/15 text-amber-300' : k === 'upi' ? 'bg-sky-500/15 text-sky-300' : 'bg-slate-500/20 text-slate-300');

interface Props { plan: PlanOffer; cat: Catalog; pending: Order | null; onClose: () => void }

export default function CheckoutModal({ plan, cat, pending, onClose }: Props) {
  const qc = useQueryClient();
  const methods: PayMethod[] = useMemo(() => cat.methods.filter((m) => plan.prices.some((p) => p.method_id === m.id)), [cat.methods, plan.prices]);
  const [methodId, setMethodId] = useState<number | null>(methods[0]?.id ?? null);
  const method = methods.find((m) => m.id === methodId) ?? methods[0] ?? null;
  const price = method ? plan.prices.find((p) => p.method_id === method.id) ?? null : null;
  const [network, setNetwork] = useState<string>('TRC20');
  const [ref, setRef] = useState('');
  const [note, setNote] = useState('');
  const [proof, setProof] = useState<string | null>(null);
  const [proofName, setProofName] = useState('');
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [accept, setAccept] = useState(false);
  const [more, setMore] = useState(false);
  const [done, setDone] = useState(false);
  const [zoom, setZoom] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const problem = method ? refProblem(method.kind, method.kind === 'usdt' ? network : null, ref) : null;

  useEffect(() => {                                                    // the page behind does not scroll while the window is open; focus moves into the window once
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    box.current?.focus({ preventScroll: true });
    return () => { document.body.style.overflow = prev; };
  }, []);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {                                                    // Esc closes (first the enlarged QR)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); if (zoom) setZoom(false); else closeRef.current(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [zoom]);

  const submit = useMutation({
    mutationFn: () => api<Order>('/api/pay/orders', { body: { plan_id: plan.id, method_id: method!.id, reference: ref, network: method!.kind === 'usdt' ? network : null, note, proof, accept_terms: accept } }),
    onSuccess: () => { setDone(true); setRef(''); setNote(''); setProof(null); setProofName(''); setTouched(false); qc.invalidateQueries({ queryKey: ['pay-orders'] }); },
  });

  const perks = [
    ...(plan.description ? plan.description.split(/\n+/).map((s) => s.trim()).filter(Boolean).slice(0, 3) : []),
    'Pay with UPI or USDT, whichever suits you',
    'A person checks your payment, then switches the plan on',
    'Renewing adds the days to the end of your current period',
  ];

  return (
    <div className="fixed inset-0 z-[300] overflow-y-auto bg-[#02050f]/75 p-3 backdrop-blur-md sm:p-6" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="co-title"
        className="relative mx-auto my-auto grid w-full max-w-[1060px] overflow-hidden rounded-[28px] border border-white/10 bg-[#0d1325] shadow-[0_40px_120px_rgba(0,0,0,.7)] outline-none md:grid-cols-[minmax(250px,330px)_1fr]">

        {/* left: what you get */}
        <aside className="relative flex flex-col gap-5 bg-gradient-to-b from-[#1b2540] via-[#0b1124] to-[#050914] p-6 md:p-8">
          <div className="flex items-center gap-4 md:block">
            <div className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-amber-300 to-orange-500 text-white shadow-[0_10px_30px_rgba(249,115,22,.45),inset_0_1px_0_rgba(255,255,255,.5)] md:h-16 md:w-16"><Crown /></div>
            <h2 id="co-title" className="text-[26px] font-extrabold leading-[1.1] tracking-tight text-white md:mt-6 md:text-[34px]">Elevate Your <span className="block bg-gradient-to-r from-amber-300 to-orange-500 bg-clip-text text-transparent">Trading Edge</span></h2>
          </div>
          <div className="text-sm font-semibold text-slate-300">{plan.name} plan{price ? ` · ${price.days} days` : ''}</div>
          <ul className="hidden space-y-3.5 md:block">
            {perks.map((p) => (
              <li key={p} className="flex items-start gap-3 text-[15px] text-slate-200"><span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-emerald-400"><Tick /></span>{p}</li>))}
          </ul>
          <div className="mt-auto hidden rounded-2xl border border-white/10 bg-white/[.04] p-4 text-[13px] leading-relaxed text-slate-300 md:block">
            <b className="text-slate-100">Safe to pay.</b> We only ask for the payment's reference, never your wallet keys or passwords. Keep your receipt until the plan is active.
          </div>
        </aside>

        {/* right: pick the method, pay, send the reference */}
        <section className="flex flex-col gap-5 p-5 sm:p-7 md:p-8">
          <div className="flex items-start justify-between gap-4">
            <p className="pt-2 text-[15px] text-slate-400">Choose how you pay to unlock <b className="text-slate-100">{plan.name}</b></p>
            <button type="button" onClick={onClose} aria-label="Close" className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-white/[.06] text-xl text-white transition hover:bg-white/[.12]">✕</button>
          </div>

          <div className={`grid gap-3 ${methods.length > 1 ? 'sm:grid-cols-2' : ''}`} role="radiogroup" aria-label="Payment method">
            {methods.map((m) => {
              const pr = plan.prices.find((p) => p.method_id === m.id)!;
              const on = method?.id === m.id;
              return (
                <button key={m.id} type="button" role="radio" aria-checked={on} onClick={() => { setMethodId(m.id); setTouched(false); }}
                  className={`relative rounded-[22px] border p-5 text-left transition ${on ? 'border-cyan-400 bg-gradient-to-br from-cyan-500/15 to-sky-900/20 shadow-[0_0_0_1px_rgba(34,211,238,.35),0_14px_40px_rgba(6,182,212,.18)]' : 'border-white/10 bg-white/[.03] hover:border-white/25'}`}>
                  <span className={`inline-block rounded-lg px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider ${kindTone(m.kind)}`}>{m.label}</span>
                  {on && <span className="absolute right-4 top-4 grid h-6 w-6 place-items-center rounded-full bg-cyan-400 text-[#04121c]"><Tick className="h-3.5 w-3.5" /></span>}
                  <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1"><span className="whitespace-nowrap text-[34px] font-extrabold leading-none tracking-tight text-white">{money(pr.amount, m.currency).replace(' USDT', '')}</span><span className="whitespace-nowrap text-xs font-bold uppercase tracking-wide text-slate-400">/ {pr.days} days</span></div>
                  <div className="mt-2 text-xs font-medium text-slate-400">Full access for {pr.days} days{m.currency === 'USDT' ? ' · paid in USDT' : ''}</div>
                </button>);
            })}
          </div>

          {method && price && (
            <div className="grid gap-6 rounded-[26px] border border-white/10 bg-white/[.03] p-5 sm:p-6 lg:grid-cols-[220px_1fr]">
              {/* the QR and the amount */}
              <div className="mx-auto w-full max-w-[240px] lg:mx-0">
                {method.qr ? (
                  <button type="button" onClick={() => setZoom(true)} className="group block w-full rounded-[22px] bg-white p-3 shadow-[0_12px_40px_rgba(0,0,0,.45)] transition hover:scale-[1.02]" aria-label="Payment QR code, click to enlarge">
                    <img src={`/api/pay/qr/${method.qr}`} alt={`${method.label} QR code`} className="mx-auto block w-full" />
                  </button>) : <div className="grid aspect-square place-items-center rounded-[22px] border border-dashed border-white/15 p-4 text-center text-sm text-slate-400">No QR code for this method yet</div>}
                {method.qr && <div className="mt-2 text-center text-[11px] font-bold uppercase tracking-wider text-slate-500">Click to zoom</div>}
                <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-3.5">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Amount to pay</div>
                  <div className="text-2xl font-extrabold tabular-nums text-white">{money(price.amount, method.currency)}</div>
                  {method.address && (
                    <div className="mt-3">
                      <div className="mb-1 flex items-center justify-between gap-2"><span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{method.kind === 'upi' ? 'UPI ID' : 'Wallet address'}</span><Copy text={method.address} /></div>
                      <div className="break-all font-mono text-[11.5px] leading-snug text-slate-200">{method.address}</div>
                    </div>)}
                </div>
              </div>

              {/* the reference form */}
              <div className="min-w-0">
                <div className="mb-3 flex items-center gap-2 text-[17px] font-extrabold text-white"><Bolt />One-step checkout</div>
                {done ? (
                  <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-5" role="status">
                    <div className="mb-3 grid h-12 w-12 place-items-center rounded-full bg-emerald-400 text-[#05210f]"><Tick className="h-6 w-6" /></div>
                    <div className="text-lg font-extrabold text-white">Payment submitted</div>
                    <p className="mt-1 text-sm text-slate-300">Your plan switches on as soon as the payment has been checked. You can follow it under <b>My payments</b>.</p>
                    <button type="button" className="mt-4 rounded-xl border border-white/15 bg-white/10 px-5 py-2.5 text-sm font-bold text-white hover:bg-white/15" onClick={onClose}>Close</button>
                  </div>
                ) : pending ? (
                  <div className="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-200">You already have a payment of <b>{money(pending.amount, pending.currency)}</b> waiting to be checked. You can send another one after it has been reviewed, or cancel it under <b>My payments</b>.</div>
                ) : (
                  <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setTouched(true); if (!problem && accept && !fileErr) submit.mutate(); }}>
                    {method.instructions && <div className="whitespace-pre-line rounded-2xl border border-white/10 bg-white/[.04] p-3.5 text-[13px] leading-relaxed text-slate-300">{method.instructions}</div>}
                    <ol className="list-decimal space-y-1 pl-5 text-[13px] text-slate-400">
                      <li>Pay exactly <b className="text-slate-100">{money(price.amount, method.currency)}</b> with the QR code or the {method.kind === 'upi' ? 'UPI ID' : 'address'}.</li>
                      <li>Copy the reference: {method.kind === 'upi' ? 'the 12-digit UTR from your UPI app' : 'the transaction hash (TXID) from your wallet or exchange'}.</li>
                      <li>Paste it below. Each reference works once.</li>
                    </ol>
                    {method.kind === 'usdt' && (
                      <label className="block"><span className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-slate-500">Network</span>
                        <select className="input !rounded-xl !border-white/10 !bg-white/5" value={network} onChange={(e) => setNetwork(e.target.value)}>{NETWORKS.map((n) => <option key={n} value={n}>{n}</option>)}</select></label>)}
                    <label className="block"><span className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-slate-500">{method.kind === 'upi' ? 'UTR / reference number' : 'Transaction hash (TXID)'}</span>
                      <input className="input !rounded-xl !border-white/10 !bg-white/5 !py-3 font-mono" value={ref} onChange={(e) => setRef(e.target.value)} onBlur={() => setTouched(true)} autoComplete="off" spellCheck={false}
                        inputMode={method.kind === 'upi' ? 'numeric' : 'text'} placeholder={method.kind === 'upi' ? '12 digits, shown in your UPI app' : 'Paste the transaction hash'} aria-invalid={touched && !!problem} />
                      {touched && problem && <span className="mt-1 block text-xs text-rose-400">{problem}</span>}</label>
                    <div>
                      <button type="button" className="text-xs font-semibold text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline" aria-expanded={more} onClick={() => setMore(!more)}>{more ? 'Hide' : 'Add'} a payment screenshot or a note (optional)</button>
                      {more && (
                        <div className="mt-2 space-y-2">
                          <input className="input !rounded-xl !border-white/10 !bg-white/5" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Screenshot of the payment" onChange={async (e) => {
                            const f = e.target.files?.[0]; setFileErr(null);
                            if (!f) { setProof(null); setProofName(''); return; }
                            const r = await readImage(f, 1_000_000);
                            if ('error' in r) { setFileErr(r.error); setProof(null); setProofName(''); e.target.value = ''; } else { setProof(r.url); setProofName(f.name); }
                          }} />
                          {fileErr && <span className="block text-xs text-rose-400">{fileErr}</span>}{proofName && <span className="block text-xs text-slate-400">{proofName}</span>}
                          <input className="input !rounded-xl !border-white/10 !bg-white/5" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Anything that helps us find the payment" aria-label="Note" />
                        </div>)}
                    </div>
                    <AcceptTerms id="acc-pay" checked={accept} onChange={setAccept} />
                    {touched && !accept && <span className="block text-xs text-rose-400">Tick the box to accept the terms before submitting.</span>}
                    {submit.isError && <ErrorBox error={submit.error} />}
                    <div className="flex flex-wrap gap-3 pt-1">
                      <button type="submit" disabled={submit.isPending || !!fileErr}
                        className="inline-flex min-h-[52px] flex-1 items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-600 px-6 text-[15px] font-extrabold uppercase tracking-wider text-white shadow-[0_14px_36px_rgba(14,116,230,.4),inset_0_1px_0_rgba(255,255,255,.3)] transition hover:brightness-110 disabled:opacity-50 sm:flex-none sm:px-8">
                        <Shield />{submit.isPending ? 'Submitting…' : 'Submit payment'}</button>
                      <button type="button" onClick={onClose} className="min-h-[52px] rounded-2xl border border-white/10 bg-white/[.06] px-6 text-[15px] font-bold text-white transition hover:bg-white/[.12]">Cancel</button>
                    </div>
                  </form>)}
                <div className="mt-5 flex items-center gap-2 border-t border-white/10 pt-4 text-[11px] font-bold uppercase tracking-wider text-slate-500"><Shield />Secure checkout · your plan is switched on once the payment is verified</div>
              </div>
            </div>)}
          {!method && <div className="rounded-2xl border border-white/10 bg-white/[.03] p-6 text-center text-slate-400">No payment method is available for this plan yet. Ask the administrator.</div>}
        </section>
      </div>

      {zoom && method?.qr && (
        <div className="fixed inset-0 z-[310] grid place-items-center bg-black/80 p-4" onClick={() => setZoom(false)} role="presentation">
          <img src={`/api/pay/qr/${method.qr}`} alt={`${method.label} QR code, enlarged`} className="max-h-[88vh] w-[min(88vw,480px)] rounded-3xl bg-white p-4" />
        </div>)}
    </div>
  );
}
