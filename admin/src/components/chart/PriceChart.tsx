import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { pct, signed } from '../../format';
import { usePref } from '../../prefs';
import type { BtTrade, ChartCandle, ChartSwing, ChartZone, MtgLadder, MtgSummary, PriceChartProps, SeriesGroup } from './types';

/**
 * THE chart of the app. Everything a trader expects, in one component used by every screen:
 *  - TradingView-style navigation: wheel = zoom at the cursor, drag = move, shift+wheel / arrows = scroll, +/- keys, range buttons, overview strip, double-click = reset
 *  - indicator overlays on the price and oscillator panes under it (legend chips hide / show each one), signal arrows, shaded condition columns, pattern boxes
 *  - backtest trades with win / loss tint, the validation period, the martingale re-entry candles and their summary
 *  - the live forming candle with a last-price line, a crosshair with price tag, a hover tooltip, and a full-screen view
 * Data comes in through props (see types.ts); `SmartChart` adds loading, strategy picking and history on top of it.
 */
const UP = '#22c55e', DOWN = '#ef4444', ACC = '#8b5cf6', GRID = '#1e2747', MUTED = '#8a93b2', AMBER = '#f59e0b', SKY = '#38bdf8', BG = '#0b1126';
const RESULT: Record<string, { fill: string; text: string; label: string }> = {
  win: { fill: 'rgba(34,197,94,0.20)', text: '#4ade80', label: 'WIN' },
  loss: { fill: 'rgba(245,158,11,0.22)', text: '#fbbf24', label: 'LOSS' },
  draw: { fill: 'rgba(148,163,184,0.16)', text: '#94a3b8', label: 'DRAW' },
};
const DEMAND = '#22c55e', SUPPLY = '#ef4444';                                  // zone colours: demand = green (buyers), supply = red (sellers)
const zoneColor = (z: ChartZone) => (z.kind === 'demand' ? DEMAND : SUPPLY);
const zoneName = (z: ChartZone) => `${z.kind === 'demand' ? 'Demand' : 'Supply'} zone${z.flip ? ' (flipped: it was the opposite kind before it broke)' : ''}`;
/** A zone lives until a candle CLOSES through it (above a supply zone, below a demand zone): that is `closed`. The strategy's own rules (used after one visit, expiry) only change the wording. */
const zoneState = (z: ChartZone, closed: boolean) => (closed ? 'closed through'
  : z.tests === 0 ? 'fresh' : `${z.tests} touch${z.tests === 1 ? '' : 'es'}${z.src === 'engulf' && z.why === 'used' ? ' · used up' : ''}`);
/** How strong a zone still is: a fresh one is at full strength and every touch (price coming back into it) wears it down: 100% → 80 → 60 → 40 → 25% (the floor). The chart fades it to match. */
const zoneStrength = (z: ChartZone) => Math.max(0.25, 1 - 0.2 * Math.max(0, z.tests));
const NO_HID: Record<string, boolean> = {};
const NONE: never[] = [];                                                              // a stable empty list, so memoised layers are not rebuilt on every render
const SWING_COLOR: Record<string, string> = { HH: '#4ade80', HL: '#4ade80', LH: '#f87171', LL: '#f87171', EH: '#94a3b8', EL: '#94a3b8', '': '#94a3b8' };
const MTG_FILL: Record<string, string> = { win: 'rgba(34,197,94,0.30)', loss: 'rgba(245,158,11,0.26)', draw: 'rgba(148,163,184,0.20)' };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MIN_VISIBLE = 12, MAP_H = 46, PAD_L = 8, PAD_R = 68, AXIS_H = 22, PANE_H = 92, PANE_GAP = 8;

const fmt = (v: number | null | undefined, ref: number) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–';
  const a = Math.abs(ref);
  return a >= 1000 ? v.toFixed(1) : a >= 100 ? v.toFixed(2) : a >= 10 ? v.toFixed(3) : a >= 1 ? v.toFixed(4) : v.toFixed(5);
};
const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
const dayLabel = (t: number) => { const d = new Date(t * 1000); return `${MONTHS[d.getMonth()]} ${d.getDate()}`; };
const full = (t: number) => `${dayLabel(t)}, ${hhmm(t)}`;
const dayOf = (t: number) => new Date(t * 1000).toDateString();

const ladderText = (l: MtgLadder): string => {
  const net = `${l.pl >= 0 ? '+' : ''}${l.pl.toFixed(2)} units`;
  if (l.outcome === 'open') return 'next candle not stored yet';
  if (l.outcome === 'lost') return `all ${l.steps.length} steps lost, ${net}`;
  if (l.outcome === 'draw') return `ended on a draw (stake refunded), ${net}`;
  return `recovered at ${l.outcome.replace('mtg', 'MTG-')}, ${net}`;
};

function linePath(values: (number | null)[], i0: number, i1: number, x: (i: number) => number, y: (v: number) => number): string {
  let d = '', pen = false;
  for (let i = Math.max(0, i0 - 1); i <= Math.min(values.length - 1, i1 + 1); i++) {
    const v = values[i];
    if (v === null || v === undefined || !Number.isFinite(v)) { pen = false; continue; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}

interface View { start: number; count: number }

/** A pill switch for the toolbar: one tap shows / hides a layer of the chart. */
function Pill({ on, set, color, title, children }: { on: boolean; set: (v: boolean) => void; color?: string; title?: string; children: React.ReactNode }) {
  return (
    <button type="button" role="switch" aria-checked={on} title={title} onClick={() => set(!on)}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-2 text-[11px] font-medium leading-none sm:py-1 transition-colors ${on ? 'text-fg' : 'border-line bg-transparent text-mut hover:text-fg'}`}
      style={on ? { borderColor: `${color ?? ACC}99`, background: `${color ?? ACC}22` } : undefined}>
      <span className="inline-block h-2 w-2 rounded-full transition-opacity" style={{ background: color ?? ACC, opacity: on ? 1 : 0.35 }} />{children}
    </button>
  );
}

/** A row of joined buttons (zoom, scroll, ranges). */
const Seg = ({ children }: { children: React.ReactNode }) => <div className="inline-flex items-stretch overflow-hidden rounded-lg border border-line bg-panel2 [&>button]:border-0 [&>button]:rounded-none [&>button+button]:border-l [&>button+button]:border-line">{children}</div>;

export function PriceChart(props: PriceChartProps) {
  const { candles, forming = null, lastPrice = null, overlays = [], panes = [], markers = [], bands = [], areas = [], boxes = [], zones = NONE as ChartZone[], swings = NONE as ChartSwing[], trades = [], payout = null, splitT = null, mtg = null,
    height = 320, initialCount = 150, compact = false, resetKey = '', toolbarLeft, toolbarRight, tooltipExtra, footer, emptyText = 'No candles to show yet.' } = props;
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const root = useRef<HTMLDivElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const all: ChartCandle[] = useMemo(() => (forming ? [...candles, forming] : candles), [candles, forming]);
  const n = all.length;

  const [w, setW] = useState(900);
  const [fs, setFs] = useState(false);
  const [vh, setVh] = useState(typeof window === 'undefined' ? 900 : window.innerHeight);
  const [view, setView] = useState<View>(() => ({ start: Math.max(0, n - Math.min(n, initialCount)), count: Math.max(1, Math.min(n, initialCount)) }));
  const [hover, setHover] = useState<{ i: number; y: number } | null>(null);
  const [drag, setDrag] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);                    // phones: the layer switches fold away (they are always shown from the sm width up)
  // every switch is saved on the account (see prefs.ts): the next visit, on any browser, opens the chart the way this person left it
  const [showBuy, setShowBuy] = usePref('chart.buy', true);
  const [showSell, setShowSell] = usePref('chart.sell', true);
  const [showResult, setShowResult] = usePref('chart.result-tint', true);
  const [showMtg, setShowMtg] = usePref('chart.mtg-candles', true);
  const [showZones, setShowZones] = usePref('chart.zones', true);
  const [showBroken, setShowBroken] = usePref('chart.past-zones', true);   // zones a candle already closed through: drawn up to that candle (switch off to see only the standing ones)
  const [showSwings, setShowSwings] = usePref('chart.structure', true);
  const [showZoneText, setShowZoneText] = usePref('chart.zone-text', true);   // "SUPPLY · fresh · 1.5 ATR"
  const [fade, setFade] = usePref('chart.zone-fade', true);              // zones fade with every touch: bold = fresh and strong, light = worn and weak
  const [showTags, setShowTags] = usePref('chart.candle-tags', true);       // ENGULFED / ENGULF / BASE / DEP on the candles that formed a zone
  const [hid, setHid] = usePref<Record<string, boolean>>('chart.hidden-lines', NO_HID);                // indicator lines the person switched off by tapping their chip

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.floor(e.contentRect.width))));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const onResize = () => setVh(window.innerHeight);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ---- full screen: the browser's real full screen when allowed, otherwise a window-filling overlay (Esc leaves either)
  const toggleFs = useCallback(async () => {
    if (!fs) {
      setFs(true);
      try { await root.current?.requestFullscreen?.(); } catch { /* the overlay still fills the window */ }
    } else {
      setFs(false);
      if (document.fullscreenElement) { try { await document.exitFullscreen(); } catch { /* ignore */ } }
    }
  }, [fs]);
  useEffect(() => {
    const onChange = () => { if (!document.fullscreenElement) setFs(false); };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);
  useEffect(() => {
    if (!fs) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFs(false); };
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = ''; };
  }, [fs]);

  // ---- geometry
  const visPanes = panes.filter((p) => !hid[p.id]);
  const paneBlock = visPanes.length * (PANE_H + PANE_GAP);
  const mainH = fs ? Math.max(260, vh - 230 - paneBlock - (mtg ? 120 : 0) - (footer ? 30 : 0)) : height;
  const H = mainH + paneBlock + AXIS_H;
  const innerW = w - PAD_L - PAD_R;
  const geom = useRef({ n, innerW });
  geom.current = { n, innerW };

  const clamp = useCallback((v: View): View => {
    const c = Math.max(Math.min(MIN_VISIBLE, n), Math.min(v.count, n));
    const maxStart = n - c + Math.max(2, c * 0.06);                    // a little empty room on the right, like a trading terminal
    return { count: Math.max(1, c), start: Math.max(0, Math.min(v.start, maxStart)) };
  }, [n]);
  const reset = useCallback(() => setView(clamp({ start: n - initialCount, count: initialCount })), [clamp, n, initialCount]);

  // follow the newest candle while the user is looking at it; keep their place when they have scrolled back; start over when the pair / timeframe changes
  const prevN = useRef(n);
  const prevKey = useRef(resetKey);
  useEffect(() => {
    if (prevKey.current !== resetKey || (prevN.current === 0 && n > 0)) {
      prevKey.current = resetKey;
      prevN.current = n;
      reset();
      return;
    }
    if (n !== prevN.current) {
      const grew = n - prevN.current;
      const was = prevN.current;
      prevN.current = n;
      setView((v) => (grew > 0 && v.start + v.count >= was - 1 ? clamp({ ...v, start: v.start + grew }) : clamp(v)));
    }
  }, [resetKey, n, reset, clamp]);

  const zoom = useCallback((factor: number, anchor = 0.5) => setView((v) => {
    const count = Math.max(Math.min(MIN_VISIBLE, n), Math.min(v.count * factor, n));
    return clamp({ count, start: v.start + (v.count - count) * anchor });
  }), [clamp, n]);
  const pan = useCallback((d: number) => setView((v) => clamp({ ...v, start: v.start + d })), [clamp]);
  const show = useCallback((count: number) => setView(clamp({ count: Math.min(count, n), start: n - Math.min(count, n) + 2 })), [clamp, n]);

  // the wheel must be a non-passive native listener, otherwise the page scrolls while the chart zooms
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const g = geom.current, rect = el.getBoundingClientRect();
      setView((v) => {
        const step = g.innerW / v.count;
        if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return clamp({ ...v, start: v.start + (e.deltaX || e.deltaY) / Math.max(step, 0.5) });
        const anchor = Math.min(1, Math.max(0, (e.clientX - rect.left - PAD_L) / g.innerW));
        const count = Math.max(Math.min(MIN_VISIBLE, g.n), Math.min(v.count * Math.exp(e.deltaY * 0.0016), g.n));
        return clamp({ count, start: v.start + (v.count - count) * anchor });
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [clamp, n, n === 0]);

  // ---- derived data
  const tf = useMemo(() => {
    if (props.tf) return props.tf;
    let best = Infinity;
    for (let i = 1; i < Math.min(n, 60); i++) { const d = all[i].t - all[i - 1].t; if (d > 0) best = Math.min(best, d); }
    return Number.isFinite(best) ? best : 60;
  }, [props.tf, all, n]);
  const idxOf = useMemo(() => new Map(all.map((c, i) => [c.t, i] as [number, number])), [all]);
  const firstAtOrAfter = useCallback((t: number) => { let lo = 0, hi = n; while (lo < hi) { const m = (lo + hi) >> 1; if (all[m].t < t) lo = m + 1; else hi = m; } return lo; }, [all, n]);
  const tradeAt = useMemo(() => {
    const m = new Map<number, BtTrade>();
    for (const t of trades) { const i = idxOf.get(t.t); if (i !== undefined) m.set(i, t); }
    return m;
  }, [trades, idxOf]);
  const tradeIdx = useMemo(() => [...tradeAt.keys()].sort((a, b) => a - b), [tradeAt]);
  const mtgAt = useMemo(() => {
    const m = new Map<number, { tr: BtTrade; st: { k: number; t: number; result: string; stake: number } }>();
    for (const t of trades) for (const st of t.mtg?.steps ?? []) { const i = idxOf.get(st.t); if (i !== undefined) m.set(i, { tr: t, st }); }
    return m;
  }, [trades, idxOf]);
  const markerAt = useMemo(() => new Map(markers.map((m) => [idxOf.get(m.t) ?? -1, m] as const)), [markers, idxOf]);
  const hasMtg = mtgAt.size > 0 || trades.some((t) => t.mtg);
  const gaps = useMemo(() => { const g: number[] = []; for (let i = 1; i < n; i++) if (all[i].t - all[i - 1].t > tf * 1.5) g.push(i); return g; }, [all, n, tf]);
  const stats = useMemo(() => {
    let wins = 0, losses = 0, draws = 0;
    for (const t of trades) { if (t.result === 'win') wins++; else if (t.result === 'loss') losses++; else draws++; }
    const settled = wins + losses;
    return { wins, losses, draws, acc: settled ? wins / settled : null, be: payout ? 1 / (1 + payout / 100) : null };
  }, [trades, payout]);

  const step = innerW / Math.max(1, view.count);
  const i0 = Math.max(0, Math.floor(view.start)), i1 = Math.min(n - 1, Math.ceil(view.start + view.count));
  const x = (i: number) => PAD_L + (i - view.start + 0.5) * step;
  const { hi, lo } = useMemo(() => {
    let h = -Infinity, l = Infinity;
    for (let i = i0; i <= i1; i++) { if (!all[i]) continue; h = Math.max(h, all[i].h); l = Math.min(l, all[i].l); }
    if (!Number.isFinite(h)) { h = 1; l = 0; }
    const pad = (h - l) * 0.12 || 1e-5;
    return { hi: h + pad, lo: l - pad };
  }, [all, i0, i1]);
  const y = (p: number) => 8 + ((hi - p) / (hi - lo)) * (mainH - 16);
  const yInv = (py: number) => hi - ((py - 8) / (mainH - 16)) * (hi - lo);

  const tickEvery = Math.max(1, Math.ceil(84 / Math.max(step, 0.1)));
  const ticks: number[] = [];
  for (let i = Math.ceil(i0 / tickEvery) * tickEvery; i <= i1; i += tickEvery) ticks.push(i);
  const dense = step < 3;
  const visibleTrades = tradeIdx.filter((i) => i >= i0 - 1 && i <= i1 && (tradeAt.get(i)!.dir > 0 ? showBuy : showSell));
  const visibleMarkers = markers.filter((m) => { const i = idxOf.get(m.t); return i !== undefined && i >= i0 - 1 && i <= i1 && (m.dir > 0 ? showBuy : showSell); });

  // ---- supply / demand zones: each runs from where it was formed until a candle CLOSES through it (above a supply zone, below a demand zone). One that no candle has
  //      closed through is still standing and runs on to the right edge, however long ago it formed (nothing cuts it at a first visit, a timer or a data gap).
  const closedN = candles.length;                                        // the candles that have closed (the forming one is not one of them)
  const zoneEnd = useMemo(() => zones.map((z) => {
    const born = z.dep ? z.dep[1] : z.t0;                                // the candle that completed the zone: only later candles can close through it
    let a = 0, b = closedN;
    while (a < b) { const m = (a + b) >> 1; if (candles[m].t < born) a = m + 1; else b = m; }
    for (let i = a + 1; i < closedN; i++) { if (candles[i].syn) continue; const c = candles[i].c; if (z.kind === 'supply' ? c > z.hi : c < z.lo) return i; }       // a stand-in for a missing candle never closes through anything
    return null;
  }), [zones, candles, closedN]);
  const zoneSpans = useMemo(() => (showZones ? zones.flatMap((z, k) => {
    const end = zoneEnd[k];
    if (end !== null && !showBroken) return [];
    const a = firstAtOrAfter(z.t0), e = end ?? n - 1;
    if (a >= n || e < i0 - 1 || a > i1 + 1) return [];
    const span = (p: [number, number] | null) => (p ? { a: firstAtOrAfter(p[0]), b: Math.min(n - 1, firstAtOrAfter(p[1] + 1) - 1) } : null);
    return [{ z, a, e, closed: end !== null, base: span(z.base), dep: span(z.dep) }];
  }) : []), [showZones, showBroken, zones, zoneEnd, firstAtOrAfter, n, i0, i1]);
  const standing = useMemo(() => zoneEnd.filter((e) => e === null).length, [zoneEnd]);
  const pastCount = zones.length - standing;

  // ---- the heavy layers are built only when the view, the data or a switch changes, never on a plain mouse move (that is what keeps hover / crosshair smooth)
  const zoneLayer = useMemo(() => (
    <g>
      {zoneSpans.map(({ z, a, e, closed }, k) => {
        const col = zoneColor(z), x0 = x(a) - step / 2, x1 = closed ? x(e) + step / 2 : w - PAD_R;
        let top = y(z.hi), bot = y(z.lo);
        if (bot - top < 7) { const mid = (top + bot) / 2; top = mid - 3.5; bot = mid + 3.5; }                // a one-candle zone can be a hair thin at this scale: keep it visible
        // strength fades the zone: fresh = bold, every touch makes it lighter (see zoneStrength); a zone a candle closed through is history: faint and dashed
        const s = closed || !fade ? 1 : zoneStrength(z), weak = !closed && fade && s <= 0.4;
        const fillOp = closed ? 0.045 : Math.max(0.04, 0.22 * s * s);
        const dash = closed || weak ? '3 4' : z.flip ? '6 4' : undefined, lineOp = closed ? 0.4 : 0.25 + 0.75 * s, lineW = closed ? 1.1 : 0.9 + 0.7 * s;
        const lx = Math.max(x0, PAD_L) + 6, room = x1 - Math.max(x0, PAD_L);
        const label = `${z.kind === 'demand' ? 'DEMAND' : 'SUPPLY'}${z.flip ? ' ⟲' : ''} · ${zoneState(z, closed)} · ${z.move.toFixed(1)} ATR`;
        return (
          <g key={`z${k}`}>
            <rect x={x0} y={top} width={Math.max(2, x1 - x0)} height={bot - top} fill={col} opacity={fillOp} />
            <g stroke={col} strokeWidth={lineW} opacity={lineOp}>
              <line x1={x0} x2={x1} y1={top} y2={top} strokeDasharray={dash} /><line x1={x0} x2={x1} y1={bot} y2={bot} strokeDasharray={dash} />
              <line x1={x0} x2={x0} y1={top} y2={bot} />{closed && <line x1={x1} x2={x1} y1={top} y2={bot} strokeWidth={2} />}
            </g>
            {showZoneText && !closed && room > 120 && bot - top > 9 && <text x={lx} y={bot - top >= 15 ? top + 11 : top - 4} fontSize="9.5" fontWeight={700} fill={col} stroke={BG} strokeWidth={3} paintOrder="stroke" opacity={0.55 + 0.45 * s}>{label}</text>}
          </g>);
      })}
    </g>
  ), [zoneSpans, step, view.start, hi, lo, mainH, w, showZoneText, fade]);                                   // eslint-disable-line react-hooks/exhaustive-deps

  const candleLayer = useMemo(() => (dense
    ? <path d={(() => { let d = ''; for (let i = i0; i <= i1; i++) d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(all[i].c).toFixed(1)}`; return d; })()} fill="none" stroke="#9aa6d1" strokeWidth={1.2} strokeLinejoin="round" />
    : <g>{all.slice(i0, i1 + 1).map((c, k) => {
      const i = i0 + k, col = c.c >= c.o ? UP : DOWN, cx = x(i), bw = Math.max(1.6, step * 0.68);
      if (c.syn) {                                                     // never received: a flat grey candle at the previous candle's close
        const sy = y(c.c);
        return <g key={c.t}><line x1={cx - bw / 2} x2={cx + bw / 2} y1={sy} y2={sy} stroke="#94a3b8" strokeWidth={2} opacity={0.75} strokeLinecap="round" />
          {step > 16 && <text x={cx} y={sy - 6} fontSize="8.5" textAnchor="middle" fill={MUTED} opacity={0.8}>filled</text>}</g>;
      }
      const top = y(Math.max(c.o, c.c)), bh = Math.max(1.4, Math.abs(y(c.o) - y(c.c)));
      return <g key={c.t} opacity={c.syn ? 0.4 : forming && i === n - 1 ? 0.65 : 1}><line x1={cx} x2={cx} y1={y(c.h)} y2={y(c.l)} stroke={col} strokeWidth={step > 6 ? 1.4 : 1} /><rect x={cx - bw / 2} y={top} width={bw} height={bh} fill={col} rx={step > 9 ? 1 : 0} /></g>;
    })}</g>), [all, i0, i1, step, view.start, hi, lo, mainH, dense, forming, n]);                              // eslint-disable-line react-hooks/exhaustive-deps

  const swingPts = useMemo(() => (showSwings ? swings.map((s) => ({ s, i: idxOf.get(s.t) })).filter((p): p is { s: ChartSwing; i: number } => p.i !== undefined && p.i >= i0 - 1 && p.i <= i1 + 1) : []), [showSwings, swings, idxOf, i0, i1]);
  const originLayer = useMemo(() => (
    <g>
      {/* the candles each zone was made from: BASE (dashed outline) and DEPARTURE (solid, tinted); a diamond where price came back to the zone */}
      {zoneSpans.map(({ z, base, dep, closed }, k) => {
        const col = zoneColor(z), box = (a: number, b: number, pad: number) => {
          let top = Infinity, bot = -Infinity;
          for (let i = Math.max(0, a); i <= Math.min(n - 1, b); i++) { top = Math.min(top, y(all[i].h)); bot = Math.max(bot, y(all[i].l)); }
          return { top: top - pad, bot: bot + pad, bx: x(a) - step / 2 + 0.5, bw: Math.max(3, step * (b - a + 1) - 1) };
        };
        const bs = base && base.b >= i0 - 1 && base.a <= i1 + 1 ? box(base.a, base.b, 4) : null;
        const ds = dep && dep.b >= i0 - 1 && dep.a <= i1 + 1 ? box(dep.a, dep.b, 4) : null;
        return (
          <g key={`zb${k}`} opacity={closed ? 0.5 : fade ? 0.4 + 0.6 * zoneStrength(z) : 1}>
            {bs && <g><rect x={bs.bx} y={bs.top} width={bs.bw} height={bs.bot - bs.top} fill="none" stroke={col} strokeWidth={1.4} strokeDasharray="4 3" rx={3} />
              {showTags && bs.bw > (z.src === 'engulf' ? 12 : 34) && <text x={bs.bx + bs.bw / 2} y={z.kind === 'demand' ? bs.bot + 11 : bs.top - 4} fontSize="9" fontWeight={800} textAnchor="middle" fill={col} stroke={BG} strokeWidth={3} paintOrder="stroke">{z.src === 'engulf' ? (step > 11 ? 'ENGULFED' : '') : 'BASE'}</text>}</g>}
            {ds && <g><rect x={ds.bx} y={ds.top} width={ds.bw} height={ds.bot - ds.top} fill={col} fillOpacity={0.14} stroke={col} strokeWidth={1.5} rx={3} />
              {showTags && ds.bw > 14 && <text x={ds.bx + ds.bw / 2} y={z.kind === 'demand' ? ds.top - 4 : ds.bot + 11} fontSize="9" fontWeight={800} textAnchor="middle" fill={col} stroke={BG} strokeWidth={3} paintOrder="stroke">{z.src === 'engulf' ? (z.kind === 'demand' ? '▲ ENGULF' : '▼ ENGULF') : z.kind === 'demand' ? '▲ DEP' : '▼ DEP'}</text>}</g>}
            {z.touches.map((t) => { const i = idxOf.get(t); return i === undefined || i < i0 || i > i1 ? null
              : <path key={t} d={`M${x(i)},${y(z.kind === 'demand' ? z.hi : z.lo) - 5} l5,5 l-5,5 l-5,-5 z`} fill={BG} stroke={col} strokeWidth={1.6} />; })}
          </g>);
      })}
      {/* market-structure swings: HH / HL green, LH / LL red */}
      {swingPts.map(({ s, i }) => {
        const col = SWING_COLOR[s.label] ?? '#94a3b8', up = s.kind === 'high';
        return (
          <g key={`sw${s.id}${s.t}${s.kind}`}>
            <circle cx={x(i)} cy={y(s.price)} r={3} fill={col} stroke={BG} strokeWidth={1} />
            {s.label && !dense && <text x={x(i)} y={up ? y(s.price) - 8 : y(s.price) + 16} fontSize="10" fontWeight={800} textAnchor="middle" fill={col} stroke={BG} strokeWidth={3} paintOrder="stroke">{s.label}</text>}
          </g>);
      })}
    </g>
  ), [zoneSpans, swingPts, step, view.start, hi, lo, mainH, all, i0, i1, n, dense, showTags, idxOf, fade]);        // eslint-disable-line react-hooks/exhaustive-deps

  // ---- interaction (pointer moves are coalesced to one update per frame)
  const dragRef = useRef<{ x: number; start: number } | null>(null);
  const moveRef = useRef<{ cx: number; cy: number; left: number; top: number } | null>(null);
  const frame = useRef(0);
  useEffect(() => () => { if (frame.current) cancelAnimationFrame(frame.current); }, []);
  const ptrs = useRef(new Map<number, number>());                         // active pointers (fingers) -> clientX
  const pinch = useRef<{ d: number; count: number; start: number; mid: number } | null>(null);
  const onDown = (e: React.PointerEvent<SVGSVGElement>) => {
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    ptrs.current.set(e.pointerId, e.clientX);
    if (ptrs.current.size === 2) {
      const [a, b] = [...ptrs.current.values()];
      pinch.current = { d: Math.max(20, Math.abs(a - b)), count: view.count, start: view.start, mid: (a + b) / 2 };
      dragRef.current = null; setHover(null);
    } else if (ptrs.current.size === 1) {
      dragRef.current = { x: e.clientX, start: view.start };
      if (e.pointerType !== 'mouse') {                                    // a tap on a touch screen shows the readout of the candle under the finger
        const r = e.currentTarget.getBoundingClientRect();
        const i = Math.floor((e.clientX - r.left - PAD_L) / step + view.start);
        setHover(i >= 0 && i < n && e.clientX - r.left >= PAD_L && e.clientX - r.left <= PAD_L + innerW ? { i, y: e.clientY - r.top } : null);
      }
    }
    setDrag(true);
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (ptrs.current.has(e.pointerId)) ptrs.current.set(e.pointerId, e.clientX);
    const pz = pinch.current;
    if (pz && ptrs.current.size === 2) {                                  // two fingers: zoom about their midpoint
      const [a, b] = [...ptrs.current.values()];
      const count = Math.max(Math.min(MIN_VISIBLE, n), Math.min(pz.count * (pz.d / Math.max(20, Math.abs(a - b))), n));
      const anchor = Math.min(1, Math.max(0, (pz.mid - r.left - PAD_L) / innerW));
      setView(clamp({ count, start: pz.start + (pz.count - count) * anchor }));
      return;
    }
    moveRef.current = { cx: e.clientX, cy: e.clientY, left: r.left, top: r.top };
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const m = moveRef.current;
      if (!m) return;
      const d = dragRef.current;
      if (d) { setView((v) => clamp({ ...v, start: d.start - (m.cx - d.x) / step })); return; }
      const i = Math.floor((m.cx - m.left - PAD_L) / step + view.start);
      setHover(i >= 0 && i < n && m.cx - m.left >= PAD_L && m.cx - m.left <= PAD_L + innerW ? { i, y: m.cy - m.top } : null);
    });
  };
  const onLeave = () => { moveRef.current = null; if (!dragRef.current) setHover(null); };
  const onUp = (e: React.PointerEvent<SVGSVGElement>) => {
    ptrs.current.delete(e.pointerId);
    if (ptrs.current.size < 2) pinch.current = null;
    if (ptrs.current.size === 1) { dragRef.current = { x: [...ptrs.current.values()][0], start: view.start }; return; }
    dragRef.current = null; setDrag(false);
  };
  const onKey = (e: React.KeyboardEvent) => {
    const k = e.key;
    if (k === 'ArrowLeft') pan(-Math.max(1, view.count / 10));
    else if (k === 'ArrowRight') pan(Math.max(1, view.count / 10));
    else if (k === '+' || k === '=') zoom(0.8);
    else if (k === '-' || k === '_') zoom(1.25);
    else if (k === 'Home') setView((v) => clamp({ ...v, start: 0 }));
    else if (k === 'End') show(view.count);
    else if (k === 'f' || k === 'F') toggleFs();
    else return;
    e.preventDefault();
  };
  const jump = (dir: 1 | -1) => {
    const mid = view.start + view.count / 2;
    const target = dir > 0 ? tradeIdx.find((i) => i > mid + 0.5) : [...tradeIdx].reverse().find((i) => i < mid - 0.5);
    if (target !== undefined) setView((v) => clamp({ ...v, start: target - v.count / 2 }));
  };

  // ---- overview strip (whole series; drag the window)
  const mapDrag = useRef<{ x: number; start: number } | null>(null);
  const mapStride = Math.max(1, Math.ceil(n / 500));
  const mapPath = useMemo(() => {
    let lo2 = Infinity, hi2 = -Infinity;
    for (const c of all) { lo2 = Math.min(lo2, c.c); hi2 = Math.max(hi2, c.c); }
    const span = hi2 - lo2 || 1;
    let d = '';
    for (let i = 0; i < n; i += mapStride) d += `${d ? 'L' : 'M'}${(i / Math.max(1, n - 1)) * innerW},${(MAP_H - 6) - ((all[i].c - lo2) / span) * (MAP_H - 12) + 3}`;
    return d;
  }, [all, n, mapStride, innerW]);
  const mapX = (i: number) => (i / Math.max(1, n)) * innerW;
  const onMapDown = (e: React.PointerEvent<SVGSVGElement>) => {
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const r = e.currentTarget.getBoundingClientRect();
    const idx = ((e.clientX - r.left - PAD_L) / innerW) * n;
    const outside = idx < view.start || idx > view.start + view.count;
    if (outside) setView((v) => clamp({ ...v, start: idx - v.count / 2 }));
    mapDrag.current = { x: e.clientX, start: outside ? idx - view.count / 2 : view.start };
  };
  const onMapMove = (e: React.PointerEvent<SVGSVGElement>) => { const d = mapDrag.current; if (d) setView((v) => clamp({ ...v, start: d.start + ((e.clientX - d.x) / innerW) * n })); };

  if (n === 0) {
    return <div className="grid place-items-center rounded-lg bg-panel2 p-10 text-center text-mut" style={{ minHeight: Math.min(height, 200) }}>{emptyText}</div>;
  }

  const hc = hover ? all[hover.i] : null;
  const hTrade = hover ? tradeAt.get(hover.i) : undefined;
  const hSettles = hover ? tradeAt.get(hover.i - 1) : undefined;
  const hMtg = hover && showMtg ? mtgAt.get(hover.i) : undefined;
  const hMarker = hover ? markerAt.get(hover.i) : undefined;
  const lastIdx = n - 1;
  const priceNow = lastPrice ?? (forming ? forming.c : null);
  const rangeBtn = (label: string, count: number) => {
    const active = Math.abs(view.count - Math.min(count, n)) < 1;
    return <button key={label} className={`btn btn-sm !px-2.5 ${active ? '!bg-acc/25 text-[#c4b5fd]' : ''}`} onClick={() => show(count)}>{label}</button>;
  };
  const groupValue = (g: SeriesGroup, i: number) => g.lines.map((l) => fmt(l.values[i], l.values[i] ?? 1)).join(' / ');
  const chip = (g: SeriesGroup) => (
    <button key={g.id} className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] sm:px-1.5 sm:py-0.5 ${hid[g.id] ? 'border-line text-mut line-through opacity-60' : 'border-line bg-panel2'}`}
      title={hid[g.id] ? 'Show on the chart' : 'Hide from the chart'} onClick={() => setHid({ ...hid, [g.id]: !hid[g.id] })}>
      <span className="inline-block h-2 w-3 rounded-sm" style={{ background: g.color }} /><b style={{ color: hid[g.id] ? undefined : g.color }}>{g.label}</b></button>
  );
  const anyTrades = tradeIdx.length > 0, anySignals = anyTrades || markers.length > 0;
  const clipId = `clip-${uid}`;

  const hZones = hover ? zoneSpans.flatMap(({ z, a, e, closed, base, dep }) => {
    const out: { z: ChartZone; what: string; closed: boolean }[] = [];
    const px = hover.y <= mainH ? yInv(hover.y) : null;
    if (px !== null && hover.i >= a && hover.i <= e && px >= z.lo - (z.hi - z.lo) * 0.2 && px <= z.hi + (z.hi - z.lo) * 0.2 && !(base && hover.i >= base.a && hover.i <= base.b) && !(dep && hover.i >= dep.a && hover.i <= dep.b)) {
      out.push({ z, closed, what: z.src === 'engulf' && z.why === 'used' ? 'inside this zone (the first visit already used it up for the strategy; it stands until a candle closes through it)' : 'inside this zone' });
    }
    const eng = z.src === 'engulf';
    if (base && hover.i >= base.a && hover.i <= base.b) out.push({ z, closed, what: eng ? `the ENGULFED ${z.kind === 'demand' ? 'red' : 'green'} candle: its full high-low range is the zone` : 'a BASE candle: the tight candles that built this zone' });
    else if (dep && hover.i >= dep.a && hover.i <= dep.b) out.push({ z, closed, what: eng ? `the candle(s) that ENGULFED it (${z.run === 1 ? 'one candle' : `${z.run} candles`}): they closed beyond the zone` : 'the DEPARTURE candle: the strong move away that made it a zone' });
    return out;
  }) : [];
  const hSwing = hover ? swings.find((s) => idxOf.get(s.t) === hover.i) : undefined;

  return (
    <div ref={root} className={fs ? 'fixed inset-0 z-[200] flex flex-col overflow-auto bg-bg p-4' : ''}>
      <div ref={wrap} className="outline-none" tabIndex={0} onKeyDown={onKey}>
        {/* toolbar */}
        <div className="mb-2 rounded-xl border border-line bg-panel2/40 p-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {toolbarLeft}
            <Seg>
              <button className="btn btn-sm !px-2.5" title="Zoom out (−)" onClick={() => zoom(1.35)}>−</button>
              <button className="btn btn-sm !px-2.5" title="Zoom in (+)" onClick={() => zoom(0.74)}>+</button>
              <button className="btn btn-sm !px-2.5" title="Scroll back (←)" onClick={() => pan(-Math.max(1, view.count / 3))}>◀</button>
              <button className="btn btn-sm !px-2.5" title="Scroll forward (→)" onClick={() => pan(Math.max(1, view.count / 3))}>▶</button>
            </Seg>
            {!compact && <Seg>{rangeBtn('50', 50)}{rangeBtn('100', 100)}{rangeBtn('250', 250)}{rangeBtn('500', 500)}{rangeBtn('All', n)}</Seg>}
            <button className="btn btn-sm" title="Jump to the newest candles" onClick={() => show(Math.min(view.count, n))}>Latest</button>
            {anyTrades && <Seg>
              <button className="btn btn-sm" title="Centre the previous trade" onClick={() => jump(-1)}>⏮ Trade</button>
              <button className="btn btn-sm" title="Centre the next trade" onClick={() => jump(1)}>Trade ⏭</button></Seg>}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {toolbarRight}
              {(anySignals || anyTrades || zones.length > 0 || swings.length > 0 || hasMtg) && <button className="btn btn-sm sm:hidden" aria-expanded={layersOpen} onClick={() => setLayersOpen(!layersOpen)}>Layers {layersOpen ? '▴' : '▾'}</button>}
              <button className="btn btn-sm" title="Back to the newest candles at the default zoom (double-click the chart too)" onClick={reset}>Reset</button>
              <button className="btn btn-sm" title={fs ? 'Leave full screen (Esc)' : 'Full screen (F)'} onClick={toggleFs}>{fs ? '✕ Exit full screen' : '⛶ Full screen'}</button>
            </div>
          </div>
          {(anySignals || anyTrades || zones.length > 0 || swings.length > 0 || hasMtg) && (
            <div className={`mt-2 flex-wrap items-center gap-1.5 border-t border-line/60 pt-2 sm:flex ${layersOpen ? 'flex' : 'hidden'}`}>
              <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-mut">Show</span>
              {anySignals && <><Pill on={showBuy} set={setShowBuy} color={UP}>▲ BUY</Pill><Pill on={showSell} set={setShowSell} color={DOWN}>▼ SELL</Pill></>}
              {anyTrades && <Pill on={showResult} set={setShowResult} color={MUTED} title="Tint the candle each trade settles on">Result tint</Pill>}
              {zones.length > 0 && <>
                <Pill on={showZones} set={setShowZones} color={DEMAND} title="Supply (red) and demand (green) zones. A zone stays on the chart until a candle CLOSES through it: above a supply zone, below a demand zone">Demand / Supply zones</Pill>
                {showZones && <>
                  {pastCount > 0 && <Pill on={showBroken} set={setShowBroken} color={MUTED} title={`Zones a candle already closed through, drawn up to that candle (${pastCount}). Off = only the ${standing} still standing`}>Past zones · {pastCount}</Pill>}
                  <Pill on={fade} set={setFade} color="#a78bfa" title="Fade a zone with every touch (price coming back into it): fresh 100%, then 80, 60, 40, and 25% at 4 or more touches. Bold = still strong, light = worn out">Fade by touches</Pill>
                  <Pill on={showZoneText} set={setShowZoneText} color={SKY} title="The text on each zone: SUPPLY · fresh · 1.5 ATR">Zone text</Pill>
                  <Pill on={showTags} set={setShowTags} color={AMBER} title="The tags on the candles that formed a zone: ENGULFED, ENGULF, BASE, DEP">Candle tags</Pill></>}</>}
              {swings.length > 0 && <Pill on={showSwings} set={setShowSwings} color="#4ade80" title="Swing highs and lows labelled higher-high / lower-high / higher-low / lower-low">HH/HL · LH/LL structure</Pill>}
              {hasMtg && <Pill on={showMtg} set={setShowMtg} color={SKY} title="Martingale re-entries after a losing trade">MTG candles</Pill>}
            </div>)}
        </div>
        {(overlays.length > 0 || panes.length > 0) && <div className="mb-2 flex flex-wrap items-center gap-1.5">{overlays.map(chip)}{panes.map(chip)}</div>}

        <div className="relative">
          <svg ref={svg} width={w} height={H} className={`block select-none rounded-xl border border-line/70 ${drag ? 'cursor-grabbing' : 'cursor-crosshair'}`} style={{ touchAction: 'pan-y', background: `linear-gradient(180deg, #0e1530 0%, ${BG} 100%)` }}
            onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onMouseLeave={onLeave} onDoubleClick={reset}>
            <defs>
              <clipPath id={`${clipId}-main`}><rect x={PAD_L} y={0} width={innerW} height={mainH} /></clipPath>
              <clipPath id={`${clipId}-all`}><rect x={PAD_L} y={0} width={innerW} height={H - AXIS_H} /></clipPath>
            </defs>

            {[0, 1, 2, 3, 4, 5].map((k) => { const p = lo + ((hi - lo) * k) / 5; return <g key={k}><line x1={PAD_L} x2={w - PAD_R} y1={y(p)} y2={y(p)} stroke={GRID} /><text x={w - PAD_R + 5} y={y(p) + 4} fontSize="10" fill={MUTED}>{fmt(p, hi)}</text></g>; })}

            <g clipPath={`url(#${clipId}-all)`}>
              {ticks.map((i) => <line key={i} x1={x(i)} x2={x(i)} y1={0} y2={H - AXIS_H} stroke={GRID} opacity={0.55} />)}

              {splitT !== null && (() => {
                const si = firstAtOrAfter(splitT);
                if (si >= n) return null;
                const x0 = Math.max(PAD_L, x(si) - step / 2);
                return <g><rect x={x0} y={0} width={Math.max(0, w - PAD_R - x0)} height={mainH} fill={ACC} opacity={0.07} /><line x1={x0} x2={x0} y1={0} y2={mainH} stroke={ACC} strokeDasharray="4 4" />
                  <text x={x0 + 6} y={14} fontSize="10.5" fontWeight={700} fill="#c4b5fd">VALIDATION PERIOD →</text></g>;
              })()}

              {gaps.filter((i) => i >= i0 && i <= i1).map((i) => <g key={i}><line x1={x(i) - step / 2} x2={x(i) - step / 2} y1={0} y2={mainH} stroke={AMBER} strokeDasharray="2 4" opacity={0.7} />
                {step > 4 && <text x={x(i) - step / 2 + 3} y={mainH - 6} fontSize="9" fill={AMBER}>{(() => { const m = Math.round((all[i].t - all[i - 1].t) / tf) - 1; return m >= 1 ? `${m} candle${m === 1 ? '' : 's'} missing` : 'data gap'; })()}</text>}</g>)}

              {bands.map((b, k) => {
                const a = firstAtOrAfter(b.t0), z = Math.min(n - 1, firstAtOrAfter(b.t1 + 1) - 1);
                if (z < a || z < i0 - 1 || a > i1 + 1) return null;
                const bx = x(a) - step / 2;
                return <g key={k}><rect x={bx} y={0} width={Math.max(2, step * (z - a + 1))} height={H - AXIS_H} fill={b.color} opacity={b.opacity ?? 0.22} />
                  {b.label && <text x={bx + Math.max(2, step * (z - a + 1)) / 2} y={mainH - 6} fontSize="10" textAnchor="middle" fill="#e6e8f2">{b.label}</text>}</g>;
              })}

              {boxes.map((b, k) => {
                const a = firstAtOrAfter(b.t0), z = Math.min(n - 1, firstAtOrAfter(b.t1 + 1) - 1);
                if (z < a || z < i0 - 1 || a > i1 + 1) return null;
                let top = Infinity, bot = -Infinity;
                for (let i = a; i <= z; i++) { top = Math.min(top, y(all[i].h)); bot = Math.max(bot, y(all[i].l)); }
                top -= 5; bot += 5;
                const bx = x(a) - step / 2 + 1, bw = step * (z - a + 1) - 2;
                return <g key={k}><rect x={bx} y={top} width={bw} height={bot - top} fill={b.color} opacity={0.14} rx={3} /><rect x={bx} y={top} width={bw} height={bot - top} fill="none" stroke={b.color} strokeWidth={1.2} rx={3} />
                  <circle cx={bx + Math.min(9, bw / 2)} cy={top - 8} r={7} fill={b.color} /><text x={bx + Math.min(9, bw / 2)} y={top - 4.5} fontSize="10" fontWeight={700} textAnchor="middle" fill="#0a0e1f">{b.label}</text></g>;
              })}

              {/* supply (red) and demand (green) zones: a shaded rectangle with solid edge lines from the base to where it ended */}
              <g clipPath={`url(#${clipId}-main)`}>{zoneLayer}</g>

              {/* price areas of live-session signals (always drawn, whatever the zone switches say) */}
              <g clipPath={`url(#${clipId}-main)`}>
                {areas.map((ar, k) => {
                  const a = firstAtOrAfter(ar.t0);
                  if (a >= n) return null;
                  const e = ar.open ? n - 1 : Math.min(n - 1, firstAtOrAfter(ar.t1 + 1) - 1);
                  if (e < a || e < i0 - 1 || a > i1 + 1) return null;
                  const x0 = x(a) - step / 2, x1 = ar.open ? w - PAD_R : x(e) + step / 2;
                  let top = y(ar.hi), bot = y(ar.lo);
                  if (bot - top < 7) { const mid = (top + bot) / 2; top = mid - 3.5; bot = mid + 3.5; }
                  return (
                    <g key={`ar${k}`}>
                      <rect x={x0} y={top} width={Math.max(2, x1 - x0)} height={bot - top} fill={ar.color} opacity={0.16} />
                      <line x1={x0} x2={x1} y1={top} y2={top} stroke={ar.color} strokeWidth={1.4} strokeDasharray={ar.dashed ? '6 4' : undefined} />
                      <line x1={x0} x2={x1} y1={bot} y2={bot} stroke={ar.color} strokeWidth={1.4} strokeDasharray={ar.dashed ? '6 4' : undefined} />
                      <line x1={x0} x2={x0} y1={top} y2={bot} stroke={ar.color} strokeWidth={1.4} />
                      {ar.label && x1 - x0 > 50 && <text x={x0 + 5} y={top - 4} fontSize="10" fontWeight={700} fill={ar.color}>{ar.label}</text>}
                    </g>);
                })}
              </g>

              {showResult && visibleTrades.map((i) => {
                const t = tradeAt.get(i)!, r = RESULT[t.result] ?? RESULT.draw, c2 = all[i + 1];
                if (!c2) return null;
                return <rect key={`b${i}`} x={x(i + 1) - Math.max(2, step * 0.5)} y={y(c2.h) - 7} width={Math.max(4, step)} height={y(c2.l) - y(c2.h) + 14} rx={3} fill={r.fill} stroke={r.text} strokeOpacity={0.55} strokeWidth={1} />;
              })}

              {showMtg && !dense && [...mtgAt.entries()].filter(([i]) => i >= i0 && i <= i1).map(([i, { st }]) => {
                const c2 = all[i], ok = st.result === 'win', bad = st.result === 'loss';
                const bw = Math.max(5, step);
                return (
                  <g key={`g${i}`}>
                    <rect x={x(i) - Math.max(2.5, step * 0.5)} y={y(c2.h) - 9} width={bw} height={y(c2.l) - y(c2.h) + 18} rx={3} fill={MTG_FILL[st.result] ?? MTG_FILL.draw} stroke={SKY} strokeWidth={1.6} strokeDasharray="4 2" />
                    {step >= 8 && <text x={x(i)} y={y(c2.h) - 13} fontSize="10" fontWeight={800} textAnchor="middle" fill={SKY}>M{st.k}<tspan fill={ok ? '#4ade80' : bad ? '#fbbf24' : '#94a3b8'}>{ok ? ' ✓' : bad ? ' ✗' : ' ='}</tspan></text>}
                  </g>);
              })}

              {candleLayer}

              <g clipPath={`url(#${clipId}-main)`}>
                {overlays.filter((g) => !hid[g.id]).map((g) => g.lines.map((l, k) => (l.style === 'dots'
                  ? l.values.slice(Math.max(0, i0 - 1), i1 + 2).map((v, j) => (v === null || v === undefined ? null : <circle key={`${g.id}${k}${j}`} cx={x(Math.max(0, i0 - 1) + j)} cy={y(v)} r={1.8} fill={l.color ?? g.color} />))
                  : <path key={`${g.id}${k}`} d={linePath(l.values, i0, i1, x, y)} fill="none" stroke={l.color ?? g.color} strokeWidth={l.color ? 1.2 : 1.6} strokeDasharray={l.style === 'dash' ? '5 4' : undefined} opacity={l.color ? 0.75 : 0.95} />)))}

                {originLayer}
              </g>

              {visibleMarkers.map((m) => {
                const i = idxOf.get(m.t)!, c = all[i], cx = x(i), s = Math.max(7, Math.min(14, step * 0.8));
                const col = m.color ?? (m.dir > 0 ? UP : DOWN);
                return m.dir > 0
                  ? <g key={`s${m.t}`}><path d={`M${cx},${y(c.l) + 5} l${-s / 2},${s * 0.95} h${s} z`} fill={col} stroke={BG} strokeWidth={0.9} />
                    {m.label && <text x={cx} y={y(c.l) + 5 + s * 0.95 + 12} fontSize="10" fontWeight={800} textAnchor="middle" fill={col}>{m.label}</text>}</g>
                  : <g key={`s${m.t}`}><path d={`M${cx},${y(c.h) - 5} l${-s / 2},${-s * 0.95} h${s} z`} fill={col} stroke={BG} strokeWidth={0.9} />
                    {m.label && <text x={cx} y={y(c.h) - 5 - s * 0.95 - 5} fontSize="10" fontWeight={800} textAnchor="middle" fill={col}>{m.label}</text>}</g>;
              })}

              {visibleTrades.map((i) => {
                const t = tradeAt.get(i)!, c = all[i], cx = x(i), buy = t.dir > 0;
                const s = Math.max(7, Math.min(14, step * 0.8)), r = RESULT[t.result] ?? RESULT.draw, tipY = buy ? y(c.l) + 5 : y(c.h) - 5;
                return (
                  <g key={`m${i}`} opacity={showMtg && t.mtg_skip ? 0.3 : 1}>
                    <path d={buy ? `M${cx},${tipY} l${-s / 2},${s * 0.95} h${s} z` : `M${cx},${tipY} l${-s / 2},${-s * 0.95} h${s} z`} fill={buy ? UP : DOWN} stroke={BG} strokeWidth={0.9} />
                    {step >= 9 && showResult && <text x={cx} y={buy ? tipY + s * 0.95 + 11 : tipY - s * 0.95 - 4} fontSize="10" fontWeight={800} textAnchor="middle" fill={r.text}>{t.result === 'win' ? 'W' : t.result === 'loss' ? 'L' : 'D'}</text>}
                  </g>);
              })}

              {priceNow !== null && priceNow >= lo && priceNow <= hi && <line x1={PAD_L} x2={w - PAD_R} y1={y(priceNow)} y2={y(priceNow)} stroke={ACC} strokeDasharray="5 5" opacity={0.9} />}

              {/* oscillator panes */}
              {visPanes.map((g, pi) => {
                const top = mainH + PANE_GAP + pi * (PANE_H + PANE_GAP) - PANE_GAP + PANE_GAP;
                const vals: number[] = [];
                for (const l of g.lines) for (let i = i0; i <= i1; i++) { const v = l.values[i]; if (v !== null && v !== undefined && Number.isFinite(v)) vals.push(v); }
                let vlo = g.range ? g.range[0] : Math.min(...vals, ...(g.levels ?? [])), vhi = g.range ? g.range[1] : Math.max(...vals, ...(g.levels ?? []));
                if (!Number.isFinite(vlo) || !Number.isFinite(vhi) || vlo === vhi) { vlo = 0; vhi = 1; }
                const padV = g.range ? 0 : (vhi - vlo) * 0.08;
                vlo -= padV; vhi += padV;
                const sy = (v: number) => top + 4 + ((vhi - v) / (vhi - vlo)) * (PANE_H - 8);
                const shown = hover ? hover.i : lastIdx;
                return (
                  <g key={g.id}>
                    <rect x={PAD_L} y={top} width={innerW} height={PANE_H} fill="#0d1328" stroke={GRID} rx={4} />
                    <text x={PAD_L + 6} y={top + 12} fontSize="10.5" fontWeight={700} fill={g.color}>{g.label}<tspan fill={MUTED} fontWeight={400}>  {groupValue(g, shown)}</tspan></text>
                    {(g.levels ?? []).map((lv) => <g key={lv}><line x1={PAD_L} x2={w - PAD_R} y1={sy(lv)} y2={sy(lv)} stroke="#2a3358" strokeDasharray="3 3" /><text x={w - PAD_R + 5} y={sy(lv) + 3} fontSize="9.5" fill={MUTED}>{lv}</text></g>)}
                    {g.lines.map((l, k) => (l.style === 'bars'
                      ? l.values.slice(Math.max(0, i0 - 1), i1 + 2).map((v, j) => { const i = Math.max(0, i0 - 1) + j; return v === null || v === undefined ? null : <rect key={`${k}${i}`} x={x(i) - step * 0.3} width={Math.max(1, step * 0.6)} y={Math.min(sy(v), sy(0))} height={Math.abs(sy(v) - sy(0))} fill={v >= 0 ? UP : DOWN} opacity={0.55} />; })
                      : <path key={k} d={linePath(l.values, i0, i1, x, sy)} fill="none" stroke={g.color} strokeWidth={1.5} strokeDasharray={l.style === 'dash' ? '5 4' : undefined} opacity={k === 0 || l.style === 'dash' ? 0.95 : 0.7} />))}
                  </g>);
              })}

              {hover && <><line x1={x(hover.i)} x2={x(hover.i)} y1={0} y2={H - AXIS_H} stroke={MUTED} strokeDasharray="3 3" opacity={0.7} />{hover.y <= mainH && <line x1={PAD_L} x2={w - PAD_R} y1={hover.y} y2={hover.y} stroke={MUTED} strokeDasharray="3 3" opacity={0.45} />}</>}
            </g>

            {/* axes */}
            {ticks.map((i) => { const newDay = i > 0 && dayOf(all[i].t) !== dayOf(all[i - 1].t); return <text key={i} x={x(i)} y={H - AXIS_H + 15} fontSize="10" textAnchor="middle" fill={newDay ? '#cbd5e1' : MUTED} fontWeight={newDay ? 700 : 400}>{newDay ? dayLabel(all[i].t) : hhmm(all[i].t)}</text>; })}
            {priceNow !== null && priceNow >= lo && priceNow <= hi && <g><rect x={w - PAD_R + 1} y={y(priceNow) - 9} width={PAD_R - 2} height={18} rx={3} fill={ACC} /><text x={w - PAD_R + 5} y={y(priceNow) + 4} fontSize="10.5" fontWeight={700} fill="#fff">{fmt(priceNow, hi)}</text></g>}
            {hover && hover.y >= 0 && hover.y <= mainH && <g><rect x={w - PAD_R + 1} y={hover.y - 8} width={PAD_R - 2} height={16} rx={3} fill="#2a3358" /><text x={w - PAD_R + 5} y={hover.y + 4} fontSize="10" fill="#e5e9ff">{fmt(yInv(hover.y), hi)}</text></g>}
            {hover && <g><rect x={Math.min(w - PAD_R - 46, Math.max(PAD_L, x(hover.i) - 23))} y={H - AXIS_H + 2} width={46} height={16} rx={3} fill="#2a3358" /><text x={Math.min(w - PAD_R - 23, Math.max(PAD_L + 23, x(hover.i)))} y={H - AXIS_H + 14} fontSize="10" textAnchor="middle" fill="#e5e9ff">{hhmm(all[hover.i].t)}</text></g>}
          </svg>

          {(() => {
            const rc = hc ?? all[lastIdx];
            if (!rc || w < 520) return null;
            if (rc.syn) return <div className="pointer-events-none absolute left-3 top-2 z-[5] rounded-md bg-[#0b1126]/75 px-2 py-1 font-mono text-[10.5px] text-warn backdrop-blur-sm">filled at previous close {fmt(rc.c, hi)}</div>;
            const up = rc.c >= rc.o, col = up ? UP : DOWN, chg = rc.o ? ((rc.c - rc.o) / rc.o) * 100 : 0;
            return (
              <div className="pointer-events-none absolute left-3 top-2 z-[5] flex items-center gap-2 rounded-md bg-[#0b1126]/75 px-2 py-1 font-mono text-[10.5px] text-mut backdrop-blur-sm">
                <span>O <b style={{ color: col }}>{fmt(rc.o, hi)}</b></span><span>H <b style={{ color: col }}>{fmt(rc.h, hi)}</b></span>
                <span>L <b style={{ color: col }}>{fmt(rc.l, hi)}</b></span><span>C <b style={{ color: col }}>{fmt(rc.c, hi)}</b></span>
                <span style={{ color: col }}>{chg >= 0 ? '+' : ''}{chg.toFixed(3)}%</span>
              </div>);
          })()}

          {hc && hover && !drag && (
            <div className="pointer-events-none absolute top-2 z-10 w-60 rounded-lg border border-line bg-panel2/95 p-2 text-[11px] shadow-xl" style={{ left: x(hover.i) > w / 2 ? Math.max(4, x(hover.i) - 252) : Math.min(w - 248, x(hover.i) + 16) }}>
              <div className="font-semibold">{full(hc.t)}{forming && hover.i === lastIdx && <span className="ml-1 font-normal text-mut">forming</span>}</div>
              {hc.syn
                ? <div className="mt-0.5 text-warn">Never received (the connection to Quotex dropped here): drawn flat at the previous close {fmt(hc.c, hi)}. The real candle replaces it when Quotex re-sends its history (switch the timeframe or pair on the Quotex tab and back).</div>
                : <div className="mt-0.5 font-mono text-mut">O {fmt(hc.o, hi)} H {fmt(hc.h, hi)}<br />L {fmt(hc.l, hi)} C {fmt(hc.c, hi)}</div>}
              {[...overlays, ...panes].filter((g) => !hid[g.id]).map((g) => <div key={g.id} className="mt-0.5 flex justify-between gap-2"><span style={{ color: g.color }}>{g.label}</span><span className="font-mono">{groupValue(g, hover.i)}</span></div>)}
              {hZones.map(({ z, what, closed }, k) => <div key={`hz${k}`} className="mt-1 border-t border-line pt-1" style={{ color: zoneColor(z) }}>
                <b>This is {what}.</b><div className="text-mut">{zoneName(z)} {fmt(z.lo, hi)} – {fmt(z.hi, hi)} · {zoneState(z, closed)}{!closed && ` · strength ${Math.round(zoneStrength(z) * 100)}%`} · departure {z.move.toFixed(1)} ATR</div></div>)}
              {hSwing && <div className="mt-1 font-semibold" style={{ color: SWING_COLOR[hSwing.label] }}>Swing {hSwing.kind} {hSwing.label && `(${hSwing.label})`} at {fmt(hSwing.price, hi)}</div>}
              {hMarker && <div className={`mt-1 font-bold ${hMarker.dir > 0 ? 'text-up' : 'text-down'}`}>{hMarker.tip ?? (hMarker.dir > 0 ? '▲ CALL signal' : '▼ PUT signal')}</div>}
              {hTrade && <div className="mt-1 border-t border-line pt-1">
                <div className={`font-bold ${hTrade.dir > 0 ? 'text-up' : 'text-down'}`}>{hTrade.dir > 0 ? '▲ BUY (CALL)' : '▼ SELL (PUT)'} signal · tier {hTrade.tier} · strength {hTrade.strength}</div>
                <div className="text-mut">enters next candle at {fmt(hTrade.entry, hi)}, settles at {fmt(hTrade.exit, hi)}</div>
                <div style={{ color: (RESULT[hTrade.result] ?? RESULT.draw).text }} className="font-bold">{(RESULT[hTrade.result] ?? RESULT.draw).label} {signed(hTrade.profit, 2)}</div>
                {showMtg && hTrade.mtg_skip && <div className="text-mut">Fired while an MTG ladder was running - cannot be taken, left out of the MTG totals.</div>}
                {showMtg && hTrade.mtg && <div className="font-semibold" style={{ color: SKY }}>MTG ladder: {ladderText(hTrade.mtg)}</div>}</div>}
              {hMtg && <div className="mt-1 border-t border-line pt-1">
                <div className="font-bold" style={{ color: SKY }}>MTG-{hMtg.st.k} · stake ×{hMtg.st.stake} · {hMtg.tr.dir > 0 ? 'BUY' : 'SELL'} from {hhmm(hMtg.tr.t)}</div>
                <div className="text-mut">Re-entry after the previous step lost: <b style={{ color: (RESULT[hMtg.st.result] ?? RESULT.draw).text }}>{(RESULT[hMtg.st.result] ?? RESULT.draw).label}</b>. Ladder: {ladderText(hMtg.tr.mtg!)}</div></div>}
              {hSettles && <div className="mt-1 border-t border-line pt-1 text-mut">This candle settles the {hSettles.dir > 0 ? 'BUY' : 'SELL'} from {hhmm(hSettles.t)}: <b style={{ color: (RESULT[hSettles.result] ?? RESULT.draw).text }}>{(RESULT[hSettles.result] ?? RESULT.draw).label}</b></div>}
              {tooltipExtra?.(hover.i)}
            </div>
          )}
        </div>

        {!compact && n > view.count && (
          <svg width={w} height={MAP_H} className="mt-1 block cursor-grab select-none rounded-md" style={{ touchAction: 'pan-y' }}
            onPointerDown={onMapDown} onPointerMove={onMapMove} onPointerUp={() => { mapDrag.current = null; }} onPointerCancel={() => { mapDrag.current = null; }}>
            <g transform={`translate(${PAD_L},0)`}>
              <rect width={innerW} height={MAP_H} rx={6} fill={BG} stroke={GRID} />
              <path d={mapPath} fill="none" stroke="#5b6794" strokeWidth={1} />
              {tradeIdx.filter((_, k) => k % Math.max(1, Math.ceil(tradeIdx.length / 300)) === 0).map((i) => <rect key={i} x={mapX(i)} y={MAP_H - 5} width={1.4} height={4} fill={tradeAt.get(i)!.dir > 0 ? UP : DOWN} />)}
              {splitT !== null && (() => { const si = firstAtOrAfter(splitT); return si >= n ? null : <rect x={mapX(si)} y={0} width={innerW - mapX(si)} height={MAP_H} fill={ACC} opacity={0.12} />; })()}
              <rect x={mapX(view.start)} y={1} width={Math.max(8, mapX(view.count))} height={MAP_H - 2} rx={5} fill={ACC} opacity={0.22} stroke={ACC} />
            </g>
          </svg>
        )}

        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-mut">
          <span>{candles.length.toLocaleString()} candles · showing {Math.round(view.count)}</span>
          {!compact && <span className="hidden md:inline">wheel = zoom · drag = move · shift+wheel / ← → = scroll · + − = zoom · F = full screen · double-click = reset</span>}
          {!compact && <span className="md:hidden">drag = move · pinch = zoom · tap = details</span>}
          {anyTrades && <span className="ml-auto">
            <b className="text-fg">{trades.length}</b> trades · <span className="text-up">{stats.wins}W</span> <span className="text-down">{stats.losses}L</span>{stats.draws ? ` ${stats.draws}D` : ''}
            {stats.acc !== null && <> · accuracy <b className={stats.be !== null && stats.acc >= stats.be ? 'text-up' : 'text-fg'}>{pct(stats.acc)}</b>{stats.be !== null && <> vs break-even {pct(stats.be)}</>}</>}
          </span>}
        </div>
        {mtg && anyTrades && <MtgPanel m={mtg} />}
        {footer}
      </div>
    </div>
  );
}

/** The one-glance cost of the martingale: how often it rescues a loss, and what that really did to the account. */
export function MtgPanel({ m }: { m: MtgSummary }) {
  const rescued = Object.entries(m.rescued);
  const rescuedTotal = rescued.reduce((a, [, v]) => a + v, 0);
  const u = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
  return (
    <div className="mt-2 rounded-lg border bg-panel2/60 p-2.5 text-[11px] leading-relaxed" style={{ borderColor: 'rgba(56,189,248,0.35)' }}>
      <div><b style={{ color: SKY }}>MTG ×{m.mult} · up to {m.steps} step{m.steps === 1 ? '' : 's'}</b> — {m.taken} trades taken{m.skipped ? ` (${m.skipped} signals fired mid-ladder and were skipped)` : ''}:
        {' '}<span className="text-down">{m.first_losses} lost</span>, of which <b className="text-up">{rescuedTotal} recovered</b>
        {rescued.map(([k, v]) => <span key={k}> · {k.replace('mtg', 'MTG-')} {v}</span>)} · <b className="text-warn">{m.lost_ladders} ladders lost</b>{m.open ? ` · ${m.open} still open` : ''}
        {m.rescue_rate !== null && <> · rescue rate <b>{(m.rescue_rate * 100).toFixed(0)}%</b></>}</div>
      <div className="text-mut">Net result in units of the first stake: with MTG <b className={m.ladder_pl >= 0 ? 'text-up' : 'text-down'}>{u(m.ladder_pl)}</b> vs flat (no MTG, same trades) <b className="text-fg">{u(m.flat_pl)}</b>
        {' '}· worst single ladder <b className="text-down">{m.worst_ladder.toFixed(2)}</b> · deepest drawdown {m.max_drawdown.toFixed(2)} · longest run of lost ladders {m.longest_lost_run} · biggest stake ×{m.stake_max.toFixed(2)}
        {' '}· a full losing ladder risks <b>{m.units_at_risk.toFixed(2)}</b> units{m.ladder_pl >= m.flat_pl ? '' : ' — MTG did worse than flat here'}.</div>
    </div>
  );
}
