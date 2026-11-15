import { useEffect, useMemo, useRef, useState } from 'react';
import { pct, prettyPair, tfName } from '../format';
import { useCoverage, usePersisted } from '../hooks';
import { Badge, Empty } from './ui';
import { SmartChart, useBtChart } from './chart/SmartChart';

/** true once the element has come within `margin` of the viewport (and stays true): charts below the fold load only when reached */
function useSeen(margin = 300) {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === 'undefined') { setSeen(true); return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect(); } }, { rootMargin: `${margin}px` });
    io.observe(el);
    return () => io.disconnect();
  }, [seen, margin]);
  return [ref, seen] as const;
}

function PairChartCard({ symbol, tf, strategyId, runId, tall, mtgSteps, mtgMult }: { symbol: string; tf: number; strategyId: number | null; runId: number | null; tall: boolean; mtgSteps: number; mtgMult: number }) {
  const [ref, seen] = useSeen();
  const [open, setOpen] = useState(true);
  const withTrades = !!(strategyId || runId);
  // the header and the chart read the same cached answer (same query key)
  const q = useBtChart({ symbol, tf, strategyId: runId ? null : strategyId, runId, mtgSteps: withTrades ? mtgSteps : 0, mtgMult, limit: 4000 }, seen && open);
  const d = q.data;
  const wins = d?.trades.filter((t) => t.result === 'win').length ?? 0, losses = d?.trades.filter((t) => t.result === 'loss').length ?? 0;
  const acc = wins + losses ? wins / (wins + losses) : null;
  const be = d?.payout ? 1 / (1 + d.payout / 100) : null;

  return (
    <div ref={ref} className="rounded-xl border border-line bg-panel p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button className="flex items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className={`text-mut transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
          <span className="font-semibold">{prettyPair(symbol)}</span><Badge>{tfName(tf)}</Badge>
        </button>
        {d?.strategy && <span className="note truncate">{d.strategy}</span>}
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          {d && d.trades.length > 0 && <>
            <span><b>{d.trades.length}</b> trades</span><span className="text-up">{wins}W</span><span className="text-down">{losses}L</span>
            {acc !== null && <Badge tone={be !== null && acc >= be ? 'up' : 'mut'} title={be !== null ? `break-even ${pct(be)}` : undefined}>{pct(acc)}</Badge>}
            {d.mtg && <Badge tone={d.mtg.ladder_pl >= 0 ? 'up' : 'down'} title={`Martingale ×${d.mtg.mult}, ${d.mtg.steps} steps: net result in units of the first stake, versus ${d.mtg.flat_pl.toFixed(2)} flat`}>MTG {d.mtg.ladder_pl >= 0 ? '+' : ''}{d.mtg.ladder_pl.toFixed(1)}u</Badge>}</>}
          {d && d.trades.length === 0 && (strategyId || runId) && <span className="note">no signals on this pair</span>}
          {d && <span className="note">{d.total.toLocaleString()} candles</span>}
        </div>
      </div>
      {open && (
        <div className="mt-3">
          {!seen ? <div className="grid animate-pulse place-items-center rounded-lg bg-panel2 text-mut" style={{ height: tall ? 460 : 320 }}>Loading {prettyPair(symbol)}…</div>
            : <SmartChart symbol={symbol} tf={tf} strategyId={strategyId} runId={runId} lockStrategy mtgSteps={mtgSteps} mtgMult={mtgMult} height={tall ? 460 : 320} enabled />}
        </div>
      )}
    </div>
  );
}

/**
 * One chart per pair, each with the full chart toolbar (zoom, scroll, ranges, full screen, indicators of the strategy), the strategy's BUY/SELL signals with win / loss,
 * and the martingale boxes. `symbols` empty = every pair that has stored candles for the timeframe.
 */
export function BacktestCharts({ symbols, tfs, strategyId, runId }: { symbols: string[]; tfs: number[]; strategyId: number | null; runId?: number | null }) {
  const cov = useCoverage();
  const [tf, setTf] = useState(tfs[0] ?? 60);
  const [cols, setCols] = useState<1 | 2>(1);
  const [tall, setTall] = useState(false);
  const [shown, setShown] = useState(4);
  const [q, setQ] = useState('');
  const [mtgSteps, setMtgSteps] = usePersisted('mtg.steps', 2);
  const [mtgMult, setMtgMult] = usePersisted('mtg.mult', 2.2);
  useEffect(() => { if (!tfs.includes(tf)) setTf(tfs[0] ?? 60); }, [tfs, tf]);
  useEffect(() => { setShown(4); }, [tf, symbols.join(','), strategyId, runId]);

  const list = useMemo(() => (cov.data ?? [])
    .filter((c) => c.tf === tf && c.candles >= 30 && (!symbols.length || symbols.includes(c.symbol)) && prettyPair(c.symbol).toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => b.candles - a.candles).map((c) => c.symbol), [cov.data, tf, symbols, q]);

  const visible = list.slice(0, shown);
  const all = !symbols.length;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        {tfs.length > 1 && <div className="flex overflow-hidden rounded-lg border border-line">{tfs.map((t) => (
          <button key={t} className={`px-3 py-1 text-xs ${t === tf ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => setTf(t)}>{tfName(t)}</button>))}</div>}
        <span className="note">{list.length} {list.length === 1 ? 'pair' : 'pairs'} {all ? '(all pairs with stored candles)' : 'selected'}{list.length > shown ? ` · showing ${shown}` : ''}</span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {(strategyId || runId) && <div className="flex items-center gap-1.5 text-xs" title="Martingale: after a losing trade, re-enter the same direction on the next candle with a bigger stake. The chart boxes those candles and the panel under it shows what it really did to the account.">
            <span style={{ color: '#38bdf8' }} className="font-semibold">MTG</span>
            <div className="flex overflow-hidden rounded-lg border border-line">{[0, 1, 2, 3].map((s) => (
              <button key={s} className={`px-2 py-1 ${s === mtgSteps ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => setMtgSteps(s)}>{s === 0 ? 'off' : s}</button>))}</div>
            {mtgSteps > 0 && <label className="flex items-center gap-1 text-mut">×<input className="input !w-14 !py-1 text-xs" type="number" min={1} max={10} step={0.1} value={mtgMult} onChange={(e) => setMtgMult(Math.min(10, Math.max(1, Number(e.target.value) || 2.2)))} /></label>}
          </div>}
          {(all || symbols.length > 4) && <input className="input !w-40 !py-1 text-xs" placeholder="Find a pair…" value={q} onChange={(e) => setQ(e.target.value)} />}
          <div className="flex overflow-hidden rounded-lg border border-line text-xs">
            <button className={`px-2.5 py-1 ${cols === 1 ? 'bg-acc text-white' : 'bg-panel2 text-mut'}`} onClick={() => setCols(1)} title="One chart per row">▭ 1 per row</button>
            <button className={`px-2.5 py-1 ${cols === 2 ? 'bg-acc text-white' : 'bg-panel2 text-mut'}`} onClick={() => setCols(2)} title="Two charts per row">▭▭ 2 per row</button></div>
          <button className="btn btn-sm" onClick={() => setTall(!tall)}>{tall ? 'Shorter charts' : 'Taller charts'}</button>
        </div>
      </div>
      {!cov.data ? <div className="grid h-40 animate-pulse place-items-center rounded-lg bg-panel2 text-mut">Loading…</div>
        : list.length === 0 ? <Empty>No stored {tfName(tf)} candles for {all ? 'any pair' : 'the selected pairs'} yet. Open a pair on Quotex and scroll its chart back to collect some.</Empty>
        : <>
          <div className={`grid gap-4 ${cols === 2 ? 'xl:grid-cols-2' : ''}`}>
            {visible.map((s) => <PairChartCard key={`${s}-${tf}-${strategyId}-${runId}`} symbol={s} tf={tf} strategyId={strategyId} runId={runId ?? null} tall={tall} mtgSteps={mtgSteps} mtgMult={mtgMult} />)}
          </div>
          {list.length > shown && <div className="mt-4 flex justify-center gap-2">
            <button className="btn" onClick={() => setShown(shown + 4)}>Show 4 more ({list.length - shown} left)</button>
            <button className="btn" onClick={() => setShown(list.length)}>Show all {list.length}</button></div>}
        </>}
    </div>
  );
}
