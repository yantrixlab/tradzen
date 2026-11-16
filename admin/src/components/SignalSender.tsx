import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ErrorBox } from './ui';
import { clock, EntryMode, priceText, SessionSignal, StatusChip, useLiveSession } from '../sessions';
import { prettyPair, tfName } from '../format';
import { LiveBadge } from './SessionDialogs';

const MIN_LEFT = 10;                    // seconds a running candle needs left for a trade to be taken on it (the server refuses less)
const MAX_AHEAD = 2 * 3600;
const pad = (n: number) => String(n).padStart(2, '0');
const hhmm = (t: number) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** What a trade runs on and until, in words: "NOW at 0.57794 · closes 03:49:00". */
function Cd({ s }: { s: number }) { return <span className="tabular-nums">{Math.floor(Math.max(0, s) / 60)}:{pad(Math.floor(Math.max(0, s)) % 60)}</span>; }

/**
 * The admin's signal sender: direction, WHEN (the running candle, the next one, the one after, or a time picked), how many candles it runs, the price it is entered at
 * (the live price, or one the admin defines) and an optional area and note. "Send heads-up" tells everybody to get ready (the candle and the area are marked on their chart);
 * "Send signal now" tells them to take it. Shown below the chart for the admin who started the live session (`any` shows it to any admin: the console).
 */
export function SignalSender({ any = false, showList = true }: { any?: boolean; showList?: boolean }) {
  const qc = useQueryClient();
  const live = useLiveSession();
  const d = live.data;
  const [dir, setDir] = useState<'CALL' | 'PUT'>('CALL');
  const [mode, setMode] = useState<EntryMode>('next');
  const [timeStr, setTimeStr] = useState('');
  const [expiry, setExpiry] = useState(1);
  const [definePx, setDefinePx] = useState(false);
  const [px, setPx] = useState('');
  const [lo, setLo] = useState('');
  const [hi, setHi] = useState('');
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);

  const sid = d?.session?.id ?? 0;
  const send = useMutation({
    mutationFn: (v: { go: boolean; body: Record<string, unknown> }) => api<SessionSignal>(`/api/sessions/${sid}/signals`, { body: { ...v.body, send: v.go } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['live-session'] }); setNote(''); },
  });
  const act = useMutation({
    mutationFn: (v: { id: number; what: 'send' | 'cancel' }) => api(`/api/sessions/${sid}/signals/${v.id}/${v.what}`, { method: 'POST', body: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-session'] }),
  });

  const tf = d?.pair?.tf ?? 60;
  const sel = d?.selected ?? null;
  const last = sel?.candles[sel.candles.length - 1];
  const livePx = sel?.last_price ?? last?.c ?? null;
  const nowS = Date.now() / 1000;
  const here = Math.floor(nowS / tf) * tf;
  const left = here + tf - nowS;

  // the candle each mode means, as seconds (the server works it out again from the clock; this is what the buttons show)
  const atSec = useMemo(() => {
    if (!timeStr) return null;
    const [h, m] = timeStr.split(':').map(Number);
    if (Number.isNaN(h) || Number.isNaN(m)) return null;
    const dt = new Date(); dt.setHours(h, m, 0, 0);
    let t = dt.getTime() / 1000;
    if (t <= Date.now() / 1000 - 60) t += 86400;                    // a time that has gone by today means tomorrow (00:05 typed at 23:50)
    return t;
  }, [timeStr, Math.floor(nowS / 60)]);                                // eslint-disable-line react-hooks/exhaustive-deps
  const entryT = mode === 'current' ? here : mode === 'next' ? here + tf : mode === 'after' ? here + 2 * tf : atSec !== null ? Math.floor(atSec / tf) * tf : null;
  const timeBad = mode === 'time' && (entryT === null || entryT <= here || entryT > here + MAX_AHEAD);
  const tooLate = mode === 'current' && left < MIN_LEFT;
  const pxNum = definePx && px !== '' ? Number(px) : null;
  const pxBad = definePx && (px === '' || !Number.isFinite(Number(px)) || Number(px) <= 0);
  const half = !!lo !== !!hi;
  const noPrice = mode === 'current' && !definePx && livePx === null;
  const can = !!sel && !half && !timeBad && !pxBad && !noPrice && !send.isPending;

  if (!d?.session || !(any || d.mine)) return null;

  const body = () => ({
    direction: dir, entry: mode, ...(mode === 'time' && atSec !== null ? { at: atSec } : {}), expiry, entry_px: pxNum,
    area_lo: lo === '' ? null : Number(lo), area_hi: hi === '' ? null : Number(hi), area_label: label, note,
  });
  const call = dir === 'CALL';
  const accent = call ? '#22c55e' : '#ef4444';
  const fill = () => { if (last) { setLo(String(last.l)); setHi(String(last.h)); } };
  const chips = [2, 3, 5, 10, 15].map((k) => here + k * tf).filter((t) => t - here <= MAX_AHEAD);
  const modeCard = (m: EntryMode, title: string, sub: React.ReactNode, hint: string) => (
    <button key={m} type="button" onClick={() => setMode(m)} title={hint}
      className={`rounded-xl border px-3 py-2 text-left transition ${mode === m ? 'border-acc bg-acc/15 shadow-[0_0_0_1px_rgba(139,92,246,0.5)]' : 'border-line bg-panel2 hover:border-acc/60'}`}>
      <div className="flex items-center justify-between text-[13px] font-bold">{title}{m === 'current' && <span className="rounded bg-rose-500/20 px-1 text-[9px] font-extrabold tracking-wider text-rose-300">NOW</span>}</div>
      <div className="mt-0.5 text-[11px] text-mut">{sub}</div>
    </button>
  );
  const summary = entryT === null ? 'Pick a time' : `${call ? '▲ CALL' : '▼ PUT'} ${sel ? prettyPair(sel.symbol) : ''} · ${mode === 'current' ? 'NOW' : `candle ${clock(entryT)}`} · ${expiry} candle${expiry > 1 ? 's' : ''}${mode === 'current' ? ` · ends ${clock(entryT + tf * expiry)}` : ` → ${clock(entryT + tf * expiry)}`}`
    + `${pxNum !== null ? ` · at ${priceText(pxNum)}` : mode === 'current' && livePx !== null ? ` · at ~${priceText(livePx)}` : ''}`;
  const mine = (d.signals ?? []).filter((s) => s.status !== 'cancelled').slice(-6).reverse();

  return (
    <section className="overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-panel to-panel2/60 shadow-lg" aria-label="Send a signal">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-panel2/60 px-4 py-2.5">
        <div className="flex items-center gap-2.5"><LiveBadge /><span className="text-sm font-bold">Send a signal</span>
          <span className="rounded-md bg-panel px-2 py-0.5 text-xs text-mut">{sel ? `${prettyPair(sel.symbol)} · ${tfName(tf)}` : 'no pair yet'}</span></div>
        <div className="flex items-center gap-3 text-xs">
          {livePx !== null && <span className="font-mono text-sm font-bold tabular-nums" title="Live price">{priceText(livePx)}</span>}
          <span className={`rounded-md border px-2 py-0.5 tabular-nums ${left < MIN_LEFT ? 'border-warn/50 text-warn' : 'border-line text-mut'}`} title="Time left in the running candle">candle closes in <Cd s={left} /></span>
        </div>
      </header>

      <div className="space-y-3.5 p-4">
        <div className="grid grid-cols-2 gap-2.5">
          {(['CALL', 'PUT'] as const).map((x) => {
            const on = dir === x, c = x === 'CALL' ? '#22c55e' : '#ef4444';
            return (
              <button key={x} type="button" onClick={() => setDir(x)} aria-pressed={on}
                className="rounded-xl border py-3 text-lg font-extrabold tracking-wide transition active:scale-[0.98]"
                style={on ? { borderColor: c, background: `${c}26`, color: c, boxShadow: `0 0 20px ${c}40` } : { borderColor: 'var(--line, #1f2747)', color: '#8a93b2', background: 'transparent' }}>
                {x === 'CALL' ? '▲ CALL' : '▼ PUT'}
              </button>);
          })}
        </div>

        <div>
          <div className="label">When</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {modeCard('current', 'Running', <>closes in <Cd s={left} /></>, 'Take it now, on the candle that is running: entered at the live price, ends when that candle closes')}
            {modeCard('next', 'Next candle', clock(here + tf), 'The candle that opens next')}
            {modeCard('after', 'Candle after', clock(here + 2 * tf), 'The candle after the next one')}
            {modeCard('time', 'At a time', mode === 'time' && entryT !== null ? clock(entryT) : 'pick a time', 'The candle that opens at a time you pick')}
          </div>
          {mode === 'time' && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel2/50 p-2">
              <input type="time" className="input !w-auto" value={timeStr} onChange={(e) => setTimeStr(e.target.value)} aria-label="Time of the candle" />
              <div className="flex flex-wrap gap-1">{chips.map((t) => <button key={t} type="button" className="btn btn-sm tabular-nums" onClick={() => setTimeStr(hhmm(t))}>{clock(t)}</button>)}</div>
              {timeBad && <span className="w-full text-xs text-warn">{timeStr ? 'Pick a candle that has not started yet, at most 2 hours ahead.' : 'Pick the time the candle opens, or tap one of the next candles.'}</span>}
            </div>)}
          {tooLate && <div className="mt-2 text-xs text-warn">Under {MIN_LEFT} seconds left in this candle: too late for a running-candle trade. Use the next candle.</div>}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="label">Runs for</div>
            <div className="inline-flex overflow-hidden rounded-lg border border-line">{[1, 2, 3].map((n) => (
              <button key={n} type="button" onClick={() => setExpiry(n)} className={`px-4 py-1.5 text-sm font-semibold transition ${expiry === n ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`}>{n} candle{n > 1 ? 's' : ''}</button>))}</div>
          </div>
          <div>
            <div className="label flex items-center justify-between">Entry price
              <span className="inline-flex overflow-hidden rounded-md border border-line normal-case tracking-normal">
                <button type="button" onClick={() => setDefinePx(false)} className={`px-2 py-0.5 text-[11px] ${!definePx ? 'bg-acc text-white' : 'bg-panel2 text-mut'}`}>Auto</button>
                <button type="button" onClick={() => { setDefinePx(true); if (px === '' && livePx !== null) setPx(String(livePx)); }} className={`px-2 py-0.5 text-[11px] ${definePx ? 'bg-acc text-white' : 'bg-panel2 text-mut'}`}>Define</button></span></div>
            {definePx
              ? <div className="flex gap-1.5"><input className="input font-mono" inputMode="decimal" value={px} onChange={(e) => setPx(e.target.value)} placeholder="price" aria-invalid={pxBad} />
                <button type="button" className="btn btn-sm whitespace-nowrap" onClick={() => livePx !== null && setPx(String(livePx))} disabled={livePx === null} title="Use the live price">Live</button></div>
              : <div className="rounded-lg border border-dashed border-line px-2.5 py-1.5 text-xs text-mut">{mode === 'current' ? <>The live price at the moment you send{livePx !== null && <b className="ml-1 font-mono text-fg">{priceText(livePx)}</b>}</> : "The candle's open"}</div>}
          </div>
        </div>

        <details className="rounded-xl border border-line bg-panel2/40 open:bg-panel2/60">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold text-mut hover:text-fg">Area and note <span className="font-normal">(optional)</span></summary>
          <div className="space-y-2 px-3 pb-3">
            <div className="flex items-end gap-2">
              <label className="flex-1"><div className="note mb-1">Area from</div><input className="input font-mono" inputMode="decimal" value={lo} onChange={(e) => setLo(e.target.value)} placeholder="price" /></label>
              <label className="flex-1"><div className="note mb-1">to</div><input className="input font-mono" inputMode="decimal" value={hi} onChange={(e) => setHi(e.target.value)} placeholder="price" /></label>
              <button type="button" className="btn btn-sm" onClick={fill} disabled={!last} title="Fill the area with the high and low of the newest candle">Last candle</button>
            </div>
            <div className="grid grid-cols-2 gap-2"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} placeholder="Area name (support, demand ...)" />
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Note for everybody" /></div>
            {half && <div className="text-xs text-warn">Give both ends of the area, or leave both empty.</div>}
          </div>
        </details>

        <div className="rounded-xl border px-3 py-2 text-sm font-semibold" style={{ borderColor: `${accent}55`, background: `${accent}12`, color: accent }}>{summary}</div>

        <div className="grid grid-cols-2 gap-2.5">
          <button type="button" className="btn justify-center py-2.5" disabled={!can || mode === 'current'} onClick={() => send.mutate({ go: false, body: body() })}
            title={mode === 'current' ? 'A running-candle trade is taken at once: there is nothing to get ready for' : 'Tell everybody to get ready: the candle and area are marked on their chart, nothing to trade yet'}>Send heads-up</button>
          <button type="button" className="btn justify-center py-2.5 font-extrabold text-white" disabled={!can || tooLate} onClick={() => send.mutate({ go: true, body: body() })}
            style={{ background: accent, borderColor: accent, boxShadow: can && !tooLate ? `0 0 22px ${accent}66` : undefined }}
            title="Tell everybody to take this trade">{mode === 'current' ? `Take ${dir} now` : 'Send signal now'}</button>
        </div>
        {!sel && <div className="text-xs text-warn">No pair yet: open a chart on Quotex or pick a pair for the session.</div>}
        {noPrice && <div className="text-xs text-warn">No live price for this pair right now. Wait for a tick or define the entry price.</div>}
        {send.isError && <ErrorBox error={send.error} />}

        {showList && mine.length > 0 && (
          <div>
            <div className="label">Your signals</div>
            <ul className="space-y-1.5">
              {mine.map((s) => (
                <li key={s.id} className="flex items-center gap-2 rounded-lg border border-line bg-panel2/50 px-2.5 py-1.5 text-xs">
                  <b className={s.direction === 'CALL' ? 'text-up' : 'text-down'}>{s.direction === 'CALL' ? '▲' : '▼'} {s.direction}</b>
                  <span className="tabular-nums text-mut">{s.entry_mode === 'current' ? 'NOW ' : ''}{clock(s.entry_t)}</span>
                  <span className="truncate text-mut">{prettyPair(s.symbol)}</span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <StatusChip status={s.status} />
                    {s.status === 'headsup' && <button className="btn btn-sm" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, what: 'send' })}>Send</button>}
                    {(s.status === 'headsup' || (s.status === 'pending' && s.entry_mode !== 'current' && nowS < s.entry_t)) && <button className="btn btn-sm" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, what: 'cancel' })}>Cancel</button>}
                  </span>
                </li>))}
            </ul>
            {act.isError && <div className="mt-2"><ErrorBox error={act.error} /></div>}
          </div>)}
      </div>
    </section>
  );
}
