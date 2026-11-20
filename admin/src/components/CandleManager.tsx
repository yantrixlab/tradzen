import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { dateTime, pct, prettyPair, tfName } from '../format';
import { Badge, Card, Empty, ErrorBox, Spinner } from './ui';

type Inv = { symbol: string; tf: number; kind: string; candles: number; first: number; last: number; flat: number; flat_share: number };
type Example = { t: number; o: number; h: number; l: number; c: number; times_usual?: number; gap_from_prev_close?: number };
type Report = {
  symbol: string; tf: number; kind: string | null; examined: number; note?: string; usual_range?: number; broken_shape?: number; flat?: number; flat_share?: number;
  spikes?: number; jumps?: number; worst_spikes?: Example[]; worst_jumps?: Example[]; worst_broken?: Example[]; verdict?: string;
};
type Preview = { dry_run: boolean; total: number; groups: { symbol: string; tf: number; kind: string; candles: number }[]; refetch: boolean; removed?: number; archive?: string };

const KIND: Record<string, string> = { tick: 'Live price feed', history: 'Quotex chart history', contrib: 'Shared by users', yahoo: 'Real-market feed (Yahoo)', derived: 'Built 5m / 15m', other: 'Other' };
const KIND_TIP: Record<string, string> = {
  tick: 'candles built from the live price ticks', history: "the Quotex chart's own history, loaded when a chart is opened", contrib: "history another user's extension shared",
  yahoo: 'the free real-market feed behind the <PAIR>_mkt pairs (not Quotex prices)', derived: '5m / 15m candles built from stored 1m candles', other: 'older or unknown source',
};
const n = (x: number) => x.toLocaleString();
const px = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(3) : Math.abs(v) >= 1 ? v.toFixed(5) : v.toFixed(6));
const toSec = (s: string): number | undefined => { const ms = s ? new Date(s).getTime() : NaN; return Number.isFinite(ms) ? Math.floor(ms / 1000) : undefined; };
const keyOf = (r: { symbol: string; tf: number; kind: string }) => `${r.symbol}|${r.tf}|${r.kind}`;

/** The removal: what would go (a dry run, always shown first), the typed confirmation, and where the removed candles are saved. */
function RemovePanel({ symbol, tf0, kind0, exact, onClose }: { symbol: string; tf0: number | null; kind0: string | null; exact?: { since: number; until: number }; onClose: () => void }) {
  const qc = useQueryClient();
  const [tf, setTf] = useState<string>(tf0 ? String(tf0) : '');
  const [kind, setKind] = useState<string>(kind0 ?? '');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [derived, setDerived] = useState(true);
  const [confirm, setConfirm] = useState('');
  const since = exact ? exact.since : toSec(from), until = exact ? exact.until : toSec(to);
  const word = symbol === '*' ? 'DELETE' : symbol;
  const body = { symbol, tf: tf ? Number(tf) : null, kind: kind || null, since: since ?? null, until: until ?? null, with_derived: derived };
  const filtered = symbol !== '*' || !!tf || !!kind || since !== undefined || until !== undefined;
  const preview = useQuery({ queryKey: ['candle-preview', body], queryFn: () => api<Preview>('/api/data/delete', { body: { ...body, dry_run: true } }), enabled: filtered, retry: false, staleTime: 0 });
  const remove = useMutation({
    mutationFn: () => api<Preview>('/api/data/delete', { body: { ...body, dry_run: false, confirm } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['inventory'] }); qc.invalidateQueries({ queryKey: ['coverage'] }); qc.invalidateQueries({ queryKey: ['candle-check'] }); },
  });
  const done = remove.data;

  return (
    <div className="space-y-3 rounded-lg border border-down/40 bg-panel2 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-down">Remove stored candles · {symbol === '*' ? 'every pair' : prettyPair(symbol)}{exact ? ` · one candle (${dateTime(exact.since)})` : ''}</div>
        <button className="btn btn-sm" onClick={onClose}>{done ? 'Close' : 'Cancel'}</button>
      </div>
      {done ? (
        <div className="space-y-2 text-sm">
          <div className="rounded-lg border border-up/40 p-2"><b className="text-up">{n(done.removed ?? 0)} candle{done.removed === 1 ? '' : 's'} removed.</b> They were saved first: <a className="underline" href={`/api/data/deleted/${encodeURIComponent(done.archive ?? '')}`} download>{done.archive}</a> (also kept in <code>data/deleted/</code> on the server).</div>
          {done.refetch && <div className="note">The real-market feed fills missing <code>_mkt</code> candles again on its next refresh (every 6 hours, or "Refresh" on the Data page). To stop that, set <code>QXS_MARKETDATA=off</code> on the server.</div>}
        </div>
      ) : (
        <>
          {!exact && (
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-xs text-mut">Timeframe<select className="input mt-1" value={tf} onChange={(e) => setTf(e.target.value)}><option value="">All timeframes</option><option value="60">1 minute</option><option value="300">5 minutes</option><option value="900">15 minutes</option></select></label>
              <label className="text-xs text-mut">Source<select className="input mt-1" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Any source</option>{Object.keys(KIND).map((k) => <option key={k} value={k}>{KIND[k]}</option>)}</select></label>
              <label className="text-xs text-mut">From (your time, optional)<input className="input mt-1" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="text-xs text-mut">To (your time, optional)<input className="input mt-1" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            </div>
          )}
          {tf === '60' && <label className="flex items-center gap-2 text-xs text-mut"><input type="checkbox" checked={derived} onChange={(e) => setDerived(e.target.checked)} /> Also remove the 5m / 15m candles built over the same stretch (they would otherwise keep the bad prices)</label>}
          {!filtered ? <div className="note">For every pair, choose a timeframe, a source or a time range first.</div>
            : preview.isError ? <ErrorBox error={preview.error} />
            : !preview.data ? <Spinner />
            : (
              <div className="text-sm">
                <div><b className="tabular-nums">{n(preview.data.total)}</b> candle{preview.data.total === 1 ? '' : 's'} would be removed{preview.data.groups.length > 1 ? ':' : '.'}</div>
                {preview.data.groups.length > 0 && <ul className="mt-1 space-y-0.5 text-xs text-mut">{preview.data.groups.slice(0, 12).map((g, i) => <li key={i}>{prettyPair(g.symbol)} · {tfName(g.tf)} · {KIND[g.kind] ?? g.kind}: {n(g.candles)}</li>)}{preview.data.groups.length > 12 && <li>… and {preview.data.groups.length - 12} more series</li>}</ul>}
                {preview.data.total > 0 && (
                  <div className="mt-3 space-y-2">
                    <div className="note">Every removed candle is saved to a file first. Backtests, charts and the AI stop seeing them at once. Signals and trades already recorded are not touched.</div>
                    <label className="block text-xs text-mut">Type <b className="font-mono text-fg">{word}</b> to confirm<input className="input mt-1 max-w-xs font-mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} /></label>
                    {remove.isError && <ErrorBox error={remove.error} />}
                    <button className="btn !border-down/60 !text-down" disabled={confirm !== word || remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? 'Removing…' : `Remove ${n(preview.data.total)} candles`}</button>
                  </div>)}
              </div>)}
        </>)}
    </div>
  );
}

function Examples({ title, rows, extra, onPick }: { title: string; rows: Example[]; extra: (e: Example) => string; onPick: (e: Example) => void }) {
  if (!rows.length) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-mut">{title}</div>
      <div className="overflow-x-auto"><table className="w-full text-xs"><tbody>{rows.map((e) => (
        <tr key={e.t}><td className="py-0.5 pr-3 whitespace-nowrap">{dateTime(e.t)}</td><td className="py-0.5 pr-3 font-mono tabular-nums text-mut">O {px(e.o)} H {px(e.h)} L {px(e.l)} C {px(e.c)}</td><td className="py-0.5 pr-3 text-warn">{extra(e)}</td>
          <td className="py-0.5 text-right"><button className="btn btn-sm" onClick={() => onPick(e)}>Remove this candle</button></td></tr>))}</tbody></table></div>
    </div>
  );
}

/** One series examined candle by candle: broken shapes, spikes, jumps, flat candles - with the worst examples to look at (or remove one by one). */
function CheckPanel({ row, onRemove, onClose }: { row: Inv; onRemove: (e: Example) => void; onClose: () => void }) {
  const q = useQuery({ queryKey: ['candle-check', row.symbol, row.tf, row.kind], queryFn: () => api<Report>(`/api/data/check?symbol=${encodeURIComponent(row.symbol)}&tf=${row.tf}&kind=${row.kind}`) });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const r = q.data;
  const bad = (r.spikes ?? 0) + (r.jumps ?? 0) + (r.broken_shape ?? 0);
  return (
    <div className="space-y-3 rounded-lg bg-panel2 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm"><Badge tone={r.verdict === 'looks clean' ? 'up' : 'warn'}>{(r.verdict ?? r.note ?? '').toUpperCase()}</Badge> <span className="note">{n(r.examined)} newest candles examined{r.usual_range ? ` · a usual candle here is ${px(r.usual_range)} tall` : ''}</span></div>
        <button className="btn btn-sm" onClick={onClose}>Hide</button>
      </div>
      {r.note ? <div className="note">{r.note}</div> : (
        <>
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge tone={r.broken_shape ? 'down' : 'mut'} title="high below the body or low above it, or a zero / negative price">{n(r.broken_shape ?? 0)} broken shape</Badge>
            <Badge tone={r.spikes ? 'warn' : 'mut'} title="a candle many times taller than this pair's usual one">{n(r.spikes ?? 0)} spikes</Badge>
            <Badge tone={r.jumps ? 'warn' : 'mut'} title="a candle that opens far from the previous close with no gap in time">{n(r.jumps ?? 0)} jumps</Badge>
            <Badge tone={(r.flat_share ?? 0) >= 0.3 ? 'down' : (r.flat_share ?? 0) >= 0.05 ? 'warn' : 'mut'} title="open = high = low = close: one price stored for the whole candle">{n(r.flat ?? 0)} flat ({pct(r.flat_share ?? 0, 1)})</Badge>
          </div>
          {bad === 0 && (r.flat_share ?? 0) < 0.05 && <div className="note">Nothing looks wrong in this series. If a chart still looks odd, the price source itself may be the cause (for the _mkt pairs these are not Quotex prices).</div>}
          <Examples title="Broken candles" rows={r.worst_broken ?? []} extra={() => 'high / low do not contain open / close'} onPick={onRemove} />
          <Examples title="Biggest spikes" rows={r.worst_spikes ?? []} extra={(e) => `${e.times_usual}× the usual height`} onPick={onRemove} />
          <Examples title="Biggest jumps" rows={r.worst_jumps ?? []} extra={(e) => `opens ${px(e.gap_from_prev_close ?? 0)} from the previous close (${e.times_usual}× usual)`} onPick={onRemove} />
        </>)}
    </div>
  );
}

/** Admin: what the candle store holds, where it came from, whether it looks right, and a safe way to remove what is wrong. */
export function CandleManager({ filter, now }: { filter: string; now: number }) {
  const inv = useQuery({ queryKey: ['inventory'], queryFn: () => api<Inv[]>('/api/data/inventory'), staleTime: 15000 });
  const [open, setOpen] = useState<{ key: string; mode: 'check' | 'remove'; exact?: { since: number; until: number } } | null>(null);
  const [bulk, setBulk] = useState(false);
  const f = filter.trim().toLowerCase();
  const rows = useMemo(() => (inv.data ?? []).filter((r) => !f || (r.symbol + prettyPair(r.symbol) + (KIND[r.kind] ?? r.kind)).toLowerCase().includes(f)), [inv.data, f]);
  const byKind = useMemo(() => {
    const m = new Map<string, { candles: number; series: number; flat: number }>();
    for (const r of inv.data ?? []) { const x = m.get(r.kind) ?? { candles: 0, series: 0, flat: 0 }; x.candles += r.candles; x.series += 1; x.flat += r.flat; m.set(r.kind, x); }
    return [...m.entries()].sort((a, b) => b[1].candles - a[1].candles);
  }, [inv.data]);
  if (inv.isError) return <ErrorBox error={inv.error} />;
  if (!inv.data) return <Spinner />;

  return (
    <div className="space-y-4">
      <Card title="What the candle store holds" right={<button className="btn btn-sm !border-down/60 !text-down" onClick={() => setBulk(!bulk)}>{bulk ? 'Close bulk removal' : 'Bulk removal…'}</button>}>
        <div className="flex flex-wrap gap-2">{byKind.map(([k, v]) => <span key={k} className="chip gap-1" title={KIND_TIP[k]}>{KIND[k] ?? k}: <b className="tabular-nums">{n(v.candles)}</b> candles in {v.series} series{v.flat ? ` · ${n(v.flat)} flat` : ''}</span>)}</div>
        <p className="note mt-2">Each row is one pair, timeframe and source. <b>Check</b> reads the candles and lists the ones that look wrong (broken shape, spikes, jumps, flat). <b>Remove…</b> counts first, asks you to type the pair's name, saves what it removes to a file, then removes it. Removing never touches signals, trades or strategies.</p>
        {bulk && <div className="mt-3"><RemovePanel symbol="*" tf0={null} kind0="yahoo" onClose={() => setBulk(false)} /></div>}
      </Card>
      <Card>
        {rows.length === 0 ? <Empty>{f ? 'No stored series match the filter.' : 'No candles stored yet.'}</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Pair', 'TF', 'Source', 'Candles', 'From', 'To', 'Flat', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{rows.map((r) => {
              const k = keyOf(r), isOpen = open?.key === k;
              return (
                <Fragment key={k}>
                  <tr><td className="td font-medium">{prettyPair(r.symbol)}</td><td className="td">{tfName(r.tf)}</td><td className="td note" title={KIND_TIP[r.kind]}>{KIND[r.kind] ?? r.kind}</td>
                    <td className="td tabular-nums">{n(r.candles)}</td><td className="td note">{dateTime(r.first)}</td><td className="td note">{dateTime(r.last)}</td>
                    <td className="td"><span className={r.flat_share >= 0.3 ? 'text-down' : r.flat_share >= 0.05 ? 'text-warn' : 'note'}>{r.flat ? pct(r.flat_share, 1) : '–'}</span></td>
                    <td className="td whitespace-nowrap">
                      <button className="btn btn-sm" onClick={() => setOpen(isOpen && open?.mode === 'check' ? null : { key: k, mode: 'check' })}>{isOpen && open?.mode === 'check' ? 'Hide' : 'Check'}</button>{' '}
                      <button className="btn btn-sm !text-down" onClick={() => setOpen(isOpen && open?.mode === 'remove' && !open.exact ? null : { key: k, mode: 'remove' })}>Remove…</button>{' '}
                      <a className="btn btn-sm" href={`/api/data/export?symbol=${encodeURIComponent(r.symbol)}&tf=${r.tf}&fmt=csv`} download title="Download this pair's candles first">CSV</a></td></tr>
                  {isOpen && (
                    <tr><td className="td" colSpan={8}>
                      {open?.mode === 'check'
                        ? <CheckPanel row={r} onClose={() => setOpen(null)} onRemove={(e) => setOpen({ key: k, mode: 'remove', exact: { since: e.t, until: e.t } })} />
                        : <RemovePanel key={`${k}|${open?.exact?.since ?? ''}`} symbol={r.symbol} tf0={r.tf} kind0={r.kind === 'derived' ? 'derived' : r.kind} exact={open?.exact} onClose={() => setOpen(null)} />}
                    </td></tr>)}
                </Fragment>);
            })}</tbody></table></div>
        )}
        <p className="note mt-3">Newest data in the store: {rows.length ? `${dateTime(Math.max(...rows.map((r) => r.last)))} (${Math.max(0, Math.round((now - Math.max(...rows.map((r) => r.last))) / 60))} min ago)` : '–'}.</p>
      </Card>
    </div>
  );
}
