import { useMemo, useState } from 'react';
import { ago, prettyPair } from '../format';
import { Coverage, LiveState, usePersisted } from '../hooks';

type SortKey = 'pair' | 'payout' | 'candles' | 'days' | 'newest' | 'complete';
interface Sort { key: SortKey; dir: 1 | -1 }
interface Row { symbol: string; payout: number | null; candles: number; days: number; last: number | null; complete: number | null; thin: boolean; live: boolean }

const THIN = 130;      // the backtester needs roughly this many candles in one unbroken stretch before it can replay anything

const COLS: { key: SortKey; label: string; title: string; align?: string }[] = [
  { key: 'pair', label: 'Pair', title: 'Sort by name' },
  { key: 'payout', label: 'Pays', title: 'Current payout. Below ~80% a strategy needs an unrealistic win rate just to break even.', align: 'text-right' },
  { key: 'candles', label: 'Candles', title: 'Stored candles for the timeframes ticked above', align: 'text-right' },
  { key: 'days', label: 'Days', title: 'Time from the oldest to the newest stored candle', align: 'text-right' },
  { key: 'newest', label: 'Newest', title: 'How recent the newest stored candle is', align: 'text-right' },
  { key: 'complete', label: 'Full', title: 'Share of candles present between the first and last (100% = no holes)', align: 'text-right' },
];

/**
 * Sortable list of every pair with stored candles and what has been collected for it, for choosing the pairs of a backtest.
 * `picked` empty means "all pairs" (the backtester's own rule); ticking pairs narrows it.
 */
export function PairPicker({ coverage, live, tfs, picked, onChange }: { coverage: Coverage[] | undefined; live: LiveState['symbols'] | undefined; tfs: number[]; picked: string[]; onChange: (p: string[]) => void }) {
  const [sort, setSort] = usePersisted<Sort>('pairs.sort', { key: 'candles', dir: -1 });
  const [q, setQ] = useState('');
  const [payOnly, setPayOnly] = usePersisted('pairs.pay80', false);
  const [deepOnly, setDeepOnly] = usePersisted('pairs.deep', false);
  const nowSec = Date.now() / 1000;

  const rows = useMemo<Row[]>(() => {
    const pay = new Map((live ?? []).map((s) => [s.symbol, s]));
    const by = new Map<string, { n: number; exp: number; first: number; last: number }>();
    for (const c of coverage ?? []) {
      if (!tfs.includes(c.tf)) continue;
      const a = by.get(c.symbol) ?? { n: 0, exp: 0, first: Infinity, last: 0 };
      a.n += c.candles; a.exp += c.expected; a.first = Math.min(a.first, c.first); a.last = Math.max(a.last, c.last);
      by.set(c.symbol, a);
    }
    return [...by.entries()].map(([symbol, a]) => ({
      symbol, payout: pay.get(symbol)?.payout ?? null, candles: a.n, days: (a.last - a.first) / 86400, last: a.last,
      complete: a.exp ? a.n / a.exp : null, thin: a.n < THIN, live: !!pay.get(symbol)?.streaming,
    }));
  }, [coverage, live, tfs]);

  const shown = useMemo(() => {
    const val = (r: Row): number | string | null => sort.key === 'pair' ? prettyPair(r.symbol) : sort.key === 'payout' ? r.payout : sort.key === 'candles' ? r.candles
      : sort.key === 'days' ? r.days : sort.key === 'newest' ? r.last : r.complete;
    const list = rows.filter((r) => prettyPair(r.symbol).toLowerCase().includes(q.toLowerCase()) && (!payOnly || (r.payout ?? 0) >= 80) && (!deepOnly || r.candles >= 1000));
    return list.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === null && y === null) return 0;
      if (x === null) return 1;                                   // missing values always sink to the bottom, whichever way you sort
      if (y === null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number)) * sort.dir;
    });
  }, [rows, q, payOnly, deepOnly, sort]);

  const set = new Set(picked);
  const allShown = shown.length > 0 && shown.every((r) => set.has(r.symbol));
  const toggle = (s: string) => onChange(set.has(s) ? picked.filter((x) => x !== s) : [...picked, s]);
  const selectShown = () => onChange([...new Set([...picked, ...shown.map((r) => r.symbol)])]);
  const clickSort = (key: SortKey) => setSort(sort.key === key ? { key, dir: sort.dir === 1 ? -1 : 1 } : { key, dir: key === 'pair' ? 1 : -1 });
  const chip = (on: boolean) => `rounded-full border px-2 py-0.5 text-[11px] ${on ? 'border-acc bg-acc/20 text-[#c4b5fd]' : 'border-line text-mut hover:text-fg'}`;
  const selectedCandles = rows.filter((r) => (picked.length ? set.has(r.symbol) : true)).reduce((a, r) => a + r.candles, 0);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="label !mb-0">Pairs · {picked.length ? `${picked.length} chosen` : `all ${rows.length}`}</span>
        <span className="note">{selectedCandles.toLocaleString()} candles</span>
      </div>
      <input className="input !py-1 text-xs" placeholder="Find a pair…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <button className={chip(payOnly)} onClick={() => setPayOnly(!payOnly)} title="Show only pairs paying 80% or more">pays ≥ 80%</button>
        <button className={chip(deepOnly)} onClick={() => setDeepOnly(!deepOnly)} title="Show only pairs with at least 1,000 stored candles">≥ 1,000 candles</button>
        <span className="mx-0.5 text-line">|</span>
        <button className="note hover:text-fg" onClick={selectShown} title="Tick every pair currently listed">select shown ({shown.length})</button>
        <button className="note hover:text-fg" onClick={() => onChange(shown.slice(0, 10).map((r) => r.symbol))} title="Tick the first 10 in the current order, e.g. the 10 with the most candles">top 10</button>
        <button className="note hover:text-fg" onClick={() => onChange([])} title="Nothing ticked = every pair is used">clear</button>
      </div>

      <div className="mt-2 max-h-[340px] overflow-auto rounded-lg border border-line bg-panel2">
        <table className="w-full text-xs">
          <thead className="sticky top-0 z-10 bg-panel2">
            <tr>
              <th className="w-6 px-1.5 py-1.5"><input type="checkbox" checked={allShown} onChange={() => (allShown ? onChange(picked.filter((s) => !shown.some((r) => r.symbol === s))) : selectShown())} title="Tick / untick everything listed" /></th>
              {COLS.map((c) => (
                <th key={c.key} className={`px-1.5 py-1.5 font-semibold ${c.align ?? 'text-left'}`} title={c.title}>
                  <button className={`whitespace-nowrap hover:text-fg ${sort.key === c.key ? 'text-[#c4b5fd]' : 'text-mut'}`} onClick={() => clickSort(c.key)}>
                    {c.label}{sort.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}</button></th>))}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && <tr><td colSpan={7} className="p-3 text-center text-mut">{rows.length === 0 ? 'No stored candles for these timeframes yet.' : 'No pair matches.'}</td></tr>}
            {shown.map((r) => (
              <tr key={r.symbol} className={`cursor-pointer border-t border-line/60 hover:bg-panel ${set.has(r.symbol) ? 'bg-acc/10' : ''} ${r.thin ? 'opacity-55' : ''}`} onClick={() => toggle(r.symbol)}
                title={r.thin ? `Only ${r.candles} candles - too few to backtest yet` : undefined}>
                <td className="px-1.5 py-1"><input type="checkbox" checked={set.has(r.symbol)} onChange={() => toggle(r.symbol)} onClick={(e) => e.stopPropagation()} /></td>
                <td className="whitespace-nowrap px-1.5 py-1 font-medium">{r.live && <span className="mr-1 text-up" title="streaming now">●</span>}{prettyPair(r.symbol)}</td>
                <td className={`px-1.5 py-1 text-right tabular-nums ${r.payout !== null && r.payout < 80 ? 'text-warn' : ''}`}>{r.payout !== null ? `${r.payout}%` : '–'}</td>
                <td className="px-1.5 py-1 text-right tabular-nums">{r.candles.toLocaleString()}</td>
                <td className="px-1.5 py-1 text-right tabular-nums text-mut">{r.days < 10 ? r.days.toFixed(1) : Math.round(r.days)}</td>
                <td className="whitespace-nowrap px-1.5 py-1 text-right text-mut">{r.last ? ago(r.last, nowSec) : '–'}</td>
                <td className={`px-1.5 py-1 text-right tabular-nums ${r.complete !== null && r.complete < 0.9 ? 'text-warn' : 'text-mut'}`}>{r.complete !== null ? `${Math.round(r.complete * 100)}%` : '–'}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      <div className="note mt-1">Click a row to tick it. Nothing ticked = all pairs. Dim rows have too few candles to backtest; amber = pays under 80% or has holes.</div>
    </div>
  );
}
