import { ReactNode, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { prettyPair } from '../format';
import { useAmountsVisible, useMoney } from '../hooks';
import { Badge, Spinner } from './ui';
import TradeChart, { CandleRow } from './TradeChart';

interface Order { id: string; amount: number | null; profit: number | null; status: string; open_t: number | null }
interface Pos { step: 0 | 1; stake: number; net: number | null; status: string; open_t: number; close_t: number; dir: string; open_price: number | null; close_price: number | null; orders: Order[] }
export interface Detail {
  trade: {
    id: string; symbol: string | null; direction: 'CALL' | 'PUT' | null; amount: number | null; open_t: number | null; expire_t: number | null; close_t: number | null;
    open_price: number | null; close_price: number | null; payout: number | null; profit: number | null; status: 'open' | 'win' | 'loss' | 'draw'; is_demo: number | null;
    source: 'followed' | 'own' | 'against'; signal: { id: number; tier: string; mode: string; strategy: string; created: number } | null;
  };
  position: Pos | null; cycle: Pos[]; tf: number; candles: CandleRow[]; window: [number, number];
  facts: { entry_offset_s: number | null; duration_s: number | null; move: number | null; move_favours_you: boolean | null; signal_lead_s: number | null };
}

const full = (t: number | null | undefined): string => (t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–');
const clock = (t: number | null | undefined): string => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–');
const hhmm = (t: number): string => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const dur = (s: number | null | undefined): string => (s === null || s === undefined ? '–' : s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
const STATUS: Record<string, [string, string]> = { win: ['up', 'WIN'], loss: ['down', 'LOSS'], draw: ['mut', 'DRAW'], open: ['warn', 'RUNNING'] };
const TXT: Record<string, string> = { up: 'text-up', down: 'text-down', warn: 'text-warn', mut: 'text-mut' };

const Eye = ({ off }: { off: boolean }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />{off && <path d="M3 3l18 18" />}
  </svg>
);

const Fact = ({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) => (
  <div className="rounded-lg border border-line bg-panel2 px-3 py-2.5">
    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">{label}</div>
    <div className={`mt-0.5 text-[15px] font-semibold tabular-nums ${tone ? TXT[tone] : ''}`}>{value}</div>
    {sub && <div className="mt-0.5 text-[11px] leading-snug text-mut">{sub}</div>}
  </div>
);

export default function TradeDetail({ id, onClose, onSelect, index, total, onPrev, onNext }: {
  id: string; onClose: () => void; onSelect: (id: string) => void; index: number; total: number; onPrev: (() => void) | null; onNext: (() => void) | null;
}) {
  const money = useMoney();
  const [visible, toggleAmounts] = useAmountsVisible();
  const [before, setBefore] = useState(20);
  const q = useQuery({
    queryKey: ['trade-detail', id, before], queryFn: () => api<Detail>(`/api/trades/${encodeURIComponent(id)}/detail?before=${before}&after=8`),
    refetchInterval: (query) => (query.state.data?.trade.status === 'open' ? 3000 : false), placeholderData: (p) => p,
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(input|select|textarea)$/i.test(el.tagName)) return;
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft' && onPrev) onPrev();
      else if (e.key === 'ArrowRight' && onNext) onNext();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose, onPrev, onNext]);

  const d = q.data;
  const t = d?.trade;
  const [stTone, stLabel] = t ? STATUS[t.status] : ['mut', ''];
  const end = t ? (t.close_t ?? t.expire_t) : null;
  const ret = t && t.status === 'win' && t.amount !== null && t.profit !== null ? t.amount + t.profit : t && t.status === 'draw' ? t.amount : t && t.status === 'loss' ? 0 : null;
  const dec = t?.open_price !== null && t?.open_price !== undefined ? Math.min(6, Math.max(2, (String(t.open_price).split('.')[1] ?? '').length)) : 5;
  const mv = d?.facts.move ?? null;
  const pctMove = mv !== null && t?.open_price ? (mv / t.open_price) * 100 : null;
  const cyc = d?.cycle ?? [];
  const cycNet = cyc.every((p) => p.status !== 'open') && cyc.length ? cyc.reduce((a, p) => a + (p.net ?? 0), 0) : null;
  const me = d?.position;

  const story = (): ReactNode => {
    if (!t || !d) return null;
    const bits: ReactNode[] = [];
    if (mv === null) bits.push('The entry or exit price was not reported for this trade.');
    else if (mv === 0) bits.push(<>Price closed exactly at your entry, so the stake was refunded (<b>draw</b>).</>);
    else bits.push(<>Price {mv > 0 ? 'rose' : 'fell'} <b className="text-fg">{Math.abs(mv).toFixed(dec)}</b>{pctMove !== null ? ` (${Math.abs(pctMove).toFixed(3)}%)` : ''} between your entry and the expiry -{' '}
      {d.facts.move_favours_you ? <b className="text-up">in your direction, so the trade won.</b> : <b className="text-down">against your {t.direction}, so the trade lost.</b>}</>);
    if (d.facts.entry_offset_s !== null && t.open_t) bits.push(<> You entered <b className="text-fg">{Math.round(d.facts.entry_offset_s)} s</b> into the {hhmm(Math.floor(t.open_t / 60) * 60)} candle, with <b className="text-fg">{dur(d.facts.duration_s)}</b> to expiry.</>);
    if (t.signal && d.facts.signal_lead_s !== null) {
      const ld = d.facts.signal_lead_s;
      bits.push(<> {t.signal.strategy} ({t.signal.mode}, tier {t.signal.tier}) had called {t.direction} {Math.abs(ld) < 2 ? 'at the same moment' : ld > 0 ? `${Math.round(ld)} s earlier` : 'just after'}.</>);
    } else if (t.source === 'against') bits.push(<> Tradzen had called the <b className="text-fg">opposite</b> direction at that time.</>);
    return bits;
  };

  const others = cyc.filter((p) => !p.orders.some((o) => o.id === id)).map((p) => ({
    open_t: p.open_t, end_t: p.close_t, open_price: p.open_price, close_price: p.close_price, direction: p.dir as 'CALL' | 'PUT', status: p.status,
  }));

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60 backdrop-blur-[2px]" onClick={onClose} role="dialog" aria-modal="true" aria-label="Trade details">
      <aside className="flex h-full w-[min(100vw,940px)] flex-col border-l border-line bg-bg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* top bar */}
        <div className="flex items-center gap-2 border-b border-line bg-panel2/80 px-3 pb-2.5 pt-[max(0.625rem,env(safe-area-inset-top))] sm:px-4">
          <button className="btn btn-sm" disabled={!onPrev} onClick={() => onPrev?.()} aria-label="Newer trade" title="Newer trade (←)">←</button>
          <button className="btn btn-sm" disabled={!onNext} onClick={() => onNext?.()} aria-label="Older trade" title="Older trade (→)">→</button>
          <span className="note ml-1">Trade {index + 1} of {total}</span>
          <span className="note ml-3 hidden sm:inline">← → to browse · Esc to close</span>
          <button className="ml-auto grid h-10 w-10 place-items-center rounded-md text-mut hover:bg-panel hover:text-fg sm:h-auto sm:w-auto sm:p-1.5" onClick={toggleAmounts} aria-pressed={visible} aria-label={visible ? 'Hide amounts' : 'Show amounts'} title={visible ? 'Hide amounts' : 'Show amounts'}><Eye off={!visible} /></button>
          <button className="btn btn-sm" onClick={onClose} autoFocus>Close</button>
        </div>

        {!d || !t ? (q.isError ? <div className="p-6 text-down">Could not load this trade.</div> : <Spinner />) : (
          <div className="flex-1 space-y-4 overflow-y-auto overscroll-contain p-3 pb-[max(1rem,env(safe-area-inset-bottom))] sm:p-5">
            {/* hero */}
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-bold tracking-tight sm:text-2xl">{t.symbol ? prettyPair(t.symbol) : 'Unknown pair'}</h2>
                  {t.direction && <span className={`chip ${t.direction === 'CALL' ? 'border-up/40 bg-up/10 text-up' : 'border-down/40 bg-down/10 text-down'}`}>{t.direction === 'CALL' ? '▲ CALL / BUY' : '▼ PUT / SELL'}</span>}
                  <Badge tone={stTone}>{stLabel}</Badge>
                  {t.is_demo ? <Badge tone="warn">DEMO</Badge> : <Badge tone="mut">REAL</Badge>}
                </div>
                <div className="note mt-1.5">Opened {full(t.open_t)} · {t.status === 'open' ? 'expires' : 'closed'} {clock(end)} · {dur(d.facts.duration_s)}</div>
              </div>
              <div className="text-right">
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">Profit / loss</div>
                <div className={`text-2xl font-bold tabular-nums sm:text-3xl ${TXT[stTone]}`}>{t.status === 'open' ? '…' : money(t.profit, true)}</div>
                {t.amount !== null && <div className="note">on a {money(t.amount)} stake{t.payout ? ` · ${t.payout}% payout` : ''}</div>}
              </div>
            </div>

            {/* chart */}
            <section className="rounded-xl border border-line bg-panel p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-mut">Where you traded <span className="ml-1 normal-case tracking-normal text-mut">· 1-minute candles</span></div>
                <div className="flex items-center gap-1" role="group" aria-label="Candles before the entry">
                  <span className="note mr-1">Show</span>
                  {[12, 20, 40, 80].map((n) => <button key={n} className={`btn btn-sm ${before === n ? 'btn-primary' : ''}`} onClick={() => setBefore(n)}>{n} before</button>)}
                </div>
              </div>
              <TradeChart candles={d.candles} tf={d.tf} signalT={t.signal && t.signal.created >= d.window[0] ? t.signal.created : null}
                trade={{ open_t: t.open_t, end_t: end, open_price: t.open_price, close_price: t.close_price, direction: t.direction, status: t.status }} others={others} />
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-mut">
                <span><i className="mr-1 inline-block h-2 w-2 rounded-full border-2 border-white bg-panel2" />entry</span>
                <span><i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: t.status === 'win' ? '#22c55e' : t.status === 'loss' ? '#ef4444' : '#f59e0b' }} />exit / result</span>
                <span><i className="mr-1 inline-block h-2.5 w-3 rounded-sm opacity-60" style={{ background: d.facts.move_favours_you === false ? '#ef4444' : '#22c55e' }} />price move against / for you</span>
                <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm border border-dashed" style={{ borderColor: stTone === 'up' ? '#22c55e' : stTone === 'down' ? '#ef4444' : '#f59e0b' }} />trade window (highlighted candle)</span>
                {t.signal && <span><i className="mr-1 inline-block h-2.5 w-0.5 bg-acc" />signal</span>}
              </div>
            </section>

            {/* what happened */}
            <section className="rounded-xl border border-line bg-panel p-4 text-sm leading-relaxed text-mut">{story()}</section>

            {/* facts */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Fact label="Stake" value={money(t.amount)} sub={me && me.orders.length > 1 ? `${me.orders.length} orders on this candle: ${money(me.stake)} in total` : undefined} />
              <Fact label={t.status === 'open' ? 'Possible return' : 'Return'} value={t.status === 'open' ? money(t.amount !== null && t.payout ? t.amount * (1 + t.payout / 100) : null) : money(ret)} sub={t.payout ? `${t.payout}% payout` : undefined} />
              <Fact label="Entry price" value={t.open_price ?? '–'} sub={`at ${clock(t.open_t)}`} />
              <Fact label="Exit price" value={t.close_price ?? '–'} sub={end ? `at ${clock(end)}` : undefined} />
              <Fact label="Price move" value={mv === null ? '–' : `${mv > 0 ? '+' : mv < 0 ? '−' : ''}${Math.abs(mv).toFixed(dec)}`} tone={d.facts.move_favours_you === null ? 'mut' : d.facts.move_favours_you ? 'up' : 'down'}
                sub={d.facts.move_favours_you === null ? undefined : d.facts.move_favours_you ? 'in your favour' : 'against you'} />
              <Fact label="Entered" value={d.facts.entry_offset_s === null ? '–' : `${Math.round(d.facts.entry_offset_s)} s in`} sub="after the candle opened" />
              <Fact label="Duration" value={dur(d.facts.duration_s)} sub="entry to expiry" />
              <Fact label="Source" value={t.source === 'followed' ? 'Followed a signal' : t.source === 'against' ? 'Against the signal' : 'Your own call'}
                tone={t.source === 'followed' ? undefined : t.source === 'against' ? 'down' : 'mut'} sub={t.signal ? `${t.signal.strategy} · tier ${t.signal.tier}` : undefined} />
            </div>

            {/* martingale cycle */}
            {cyc.length > 1 && (
              <section className="rounded-xl border border-line bg-panel p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-mut">Martingale cycle</div>
                  {cycNet !== null && <div className={`text-sm font-bold tabular-nums ${cycNet > 0 ? 'text-up' : cycNet < 0 ? 'text-down' : 'text-mut'}`}>Cycle result {money(cycNet, true)}</div>}
                </div>
                <ol className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
                  {cyc.map((p, k) => {
                    const current = p.orders.some((o) => o.id === id);
                    const [pt] = STATUS[p.status] ?? ['mut'];
                    return (
                      <li key={p.open_t} className="flex flex-1 items-center gap-2">
                        {k > 0 && <span className="text-mut" aria-hidden="true">→</span>}
                        <button type="button" disabled={current} onClick={() => onSelect(p.orders[0].id)}
                          className={`flex-1 rounded-lg border p-3 text-left transition ${current ? 'border-acc bg-acc/10' : 'border-line bg-panel2 hover:border-acc/60'}`}>
                          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">{p.step ? 'MTG step' : 'First trade'}{current ? ' · viewing' : ''}</div>
                          <div className="mt-1 flex items-baseline justify-between gap-2">
                            <span className="font-semibold tabular-nums">{money(p.stake)}{cyc[0].stake && p.step ? <span className="ml-1 text-xs font-normal text-mut">({(p.stake / cyc[0].stake).toFixed(2).replace(/\.?0+$/, '')}×)</span> : null}</span>
                            <span className={`font-bold tabular-nums ${TXT[pt]}`}>{p.status === 'open' ? 'running' : money(p.net, true)}</span>
                          </div>
                          <div className="note mt-0.5">{clock(p.open_t)} · {p.orders.length > 1 ? `${p.orders.length} orders` : '1 order'}</div>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </section>
            )}

            {me && me.orders.length > 1 && (
              <section className="rounded-xl border border-line bg-panel p-4">
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-mut">Orders in this position</div>
                <div className="divide-y divide-line text-sm">
                  {me.orders.map((o) => (
                    <button key={o.id} type="button" onClick={() => o.id !== id && onSelect(o.id)} className={`flex w-full items-center justify-between py-2 text-left ${o.id === id ? 'text-fg' : 'text-mut hover:text-fg'}`}>
                      <span className="tabular-nums">{clock(o.open_t)} · {money(o.amount)}{o.id === id ? <span className="ml-2 text-xs text-acc">viewing</span> : null}</span>
                      <span className={`font-semibold tabular-nums ${TXT[STATUS[o.status]?.[0] ?? 'mut']}`}>{money(o.profit, true)}</span>
                    </button>))}
                </div>
              </section>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pb-2 text-[11px] text-mut">
              <span>Order ID <code className="rounded bg-panel2 px-1.5 py-0.5">{t.id}</code></span>
              {t.signal && <span>Signal #{t.signal.id} · {t.signal.mode}</span>}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
