import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, Candle } from '../../api';
import { useStrategies } from '../../hooks';
import { ChartData, indicatorGroup } from '../StrategyChart';
import { ErrorBox } from '../ui';
import { PriceChart } from './PriceChart';
import { catalogItem, IndicatorMenu } from './IndicatorMenu';
import { usePref } from '../../prefs';
import type { BtTrade, ChartArea, ChartBand, ChartCandle, ChartMarker, MtgSummary, SeriesGroup } from './types';

/** What /api/backtests/chart returns: a pair's candles, and (with a strategy) the trades it would have taken, their results, the validation split and the MTG summary. */
export interface BtChart {
  symbol: string; tf: number; total: number; first: number | null; last: number | null;
  candles: [number, number, number, number, number][];            // t, open, high, low, close
  trades: BtTrade[]; split_t: number | null; note: string | null; strategy: string | null; payout: number | null; mtg?: MtgSummary;
}
export interface BtChartParams { symbol: string; tf: number; strategyId: number | null; runId: number | null; mtgSteps: number; mtgMult: number; limit: number }

/** One shared query for a pair's history + a strategy's trades (react-query de-duplicates it, so a card header and its chart read the same answer). */
export const useBtChart = (p: BtChartParams, enabled = true, refetchMs: number | false = false) => useQuery({
  queryKey: ['bt-chart', p.symbol, p.tf, p.runId ? null : p.strategyId, p.runId, p.mtgSteps, p.mtgMult, p.limit],
  enabled: enabled && !!p.symbol, staleTime: 60_000, retry: false, refetchInterval: refetchMs,
  queryFn: () => api<BtChart>('/api/backtests/chart', { body: { symbol: p.symbol, tf: p.tf, strategy_id: p.runId ? null : p.strategyId, run_id: p.runId, limit: p.limit, mtg_steps: p.mtgSteps, mtg_mult: p.mtgMult } }),
});

export interface SmartChartProps {
  symbol: string; tf: number;
  strategyId?: number | null;            // strategy applied at first (its trades, results and indicators are drawn)
  runId?: number | null;                 // show the trades a finished backtest run stored instead of replaying the strategy
  lockStrategy?: boolean;                // the page chooses the strategy: hide the picker
  mtgSteps?: number; mtgMult?: number;   // martingale controlled by the page; omit to get the chart's own MTG buttons
  live?: { candles: Candle[]; forming: Candle | null; lastPrice: number | null };   // the live feed: newest candles, the forming one, the last price
  overlay?: { markers?: ChartMarker[]; bands?: ChartBand[]; areas?: ChartArea[] };     // extra marks drawn over the candles (the signals of a live session)
  height?: number; compact?: boolean; limit?: number; initialCount?: number; enabled?: boolean;
}

const NO_PICKS: string[] = [];
const MAX_BRIDGE = 10;
const toCandle = (c: Candle): ChartCandle => ({ t: c.t, o: c.o, h: c.h, l: c.l, c: c.c });

/**
 * The chart to use whenever a screen just has a pair and a timeframe: loads the history, lets the user apply ANY strategy (its signals, trades with win / loss,
 * validation period, martingale, and indicators incl. supply/demand zones), keeps up with the live candle, and has every navigation feature of PriceChart.
 */
export function SmartChart({ symbol, tf, strategyId = null, runId = null, lockStrategy = false, mtgSteps, mtgMult, live, overlay, height = 320, compact = false, limit = 4000, initialCount = 150, enabled = true }: SmartChartProps) {
  const strategies = useStrategies();
  const [sid, setSid] = useState<number | null>(strategyId);
  // saved on the account (prefs.ts): the strategy's indicators on / off, the martingale buttons, and the indicators the person picked from the menu
  const [showInd, setShowInd] = usePref('chart.strategy-indicators', true);
  const [lSteps, setLSteps] = usePref('chart.mtg-steps', 0);
  const [lMult, setLMult] = usePref('chart.mtg-mult', 2.2);
  const [picked, setPicked] = usePref<string[]>('chart.indicators', NO_PICKS);
  useEffect(() => { setSid(strategyId); }, [strategyId]);
  const applied = lockStrategy ? strategyId : sid;
  const steps = mtgSteps ?? lSteps, mult = mtgMult ?? lMult;

  const q = useBtChart({ symbol, tf, strategyId: applied, runId, mtgSteps: applied || runId ? steps : 0, mtgMult: mult, limit }, enabled, live ? 30_000 : false);

  // indicators of the applied strategy (supply/demand zones, EMAs, RSI ...): computed by the server for the newest 400 candles
  // The chart's own features (supply/demand zones with their base candles, HH/HL/LH/LL structure) are always available, with or without a strategy: `marks: true`
  // asks the server to add them when the strategy does not declare those indicators itself (a strategy that does gets its own settings).
  const spec = useQuery({ queryKey: ['strategy-spec', applied], enabled: !!applied && !compact, staleTime: 60_000, queryFn: () => api<{ spec: Record<string, unknown>; version: number }>(`/api/strategies/${applied}`) });
  const isMeta = !!spec.data?.spec && (spec.data.spec as { kind?: string }).kind === 'meta';
  const ind = useQuery({
    queryKey: ['smart-ind', symbol, tf, applied, spec.data?.version, limit], enabled: !compact && !!symbol && (!applied || !!spec.data), staleTime: 15_000, retry: false, refetchInterval: live ? 30_000 : false,
    queryFn: () => api<ChartData>('/api/strategies/chart', { body: { spec: applied && spec.data && !isMeta ? spec.data.spec : {}, symbol, tf, count: Math.max(limit, 400), exprs: {}, patterns: {}, marks: true, all: true } }),
  });

  // the indicators picked from the menu are asked for on their own (the strategy's signals and zones are never touched by them)
  const mine = useMemo(() => picked.map((k) => catalogItem(k)).filter((c): c is NonNullable<typeof c> => !!c), [picked]);
  const mineKey = mine.map((c) => c.key).join(',');
  const own = useQuery({
    queryKey: ['smart-own-ind', symbol, tf, mineKey, limit], enabled: !compact && !!symbol && mine.length > 0, staleTime: 15_000, retry: false, refetchInterval: live ? 30_000 : false,
    queryFn: () => api<ChartData>('/api/strategies/chart', { body: { spec: { indicators: mine.map((c) => ({ id: `u_${c.key}`, type: c.type, ...c.params })) }, symbol, tf, count: Math.max(limit, 400), exprs: {}, patterns: {}, marks: false, all: true } }),
  });

  const candles = useMemo<ChartCandle[]>(() => {
    const m = new Map<number, ChartCandle>();
    for (const r of q.data?.candles ?? []) m.set(r[0], { t: r[0], o: r[1], h: r[2], l: r[3], c: r[4] });
    for (const c of live?.candles ?? []) m.set(c.t, toCandle(c));        // the live feed is newer than the stored history
    const sorted = [...m.values()].sort((a, b) => a.t - b.t);
    // A hole of up to MAX_BRIDGE candles (the connection to Quotex dropped and its history had not caught up) is filled with flat candles at the previous candle's close, so
    // the time axis stays true and the chart reads continuously; they are replaced by the real candles as soon as Quotex re-sends its history. Bigger holes stay a marked gap.
    const out: ChartCandle[] = [];
    for (const c of sorted) {
      const prev = out[out.length - 1];
      if (prev && tf > 0 && (c.t - prev.t) % tf === 0) {
        const miss = (c.t - prev.t) / tf - 1;
        if (miss >= 1 && miss <= MAX_BRIDGE) for (let j = 1; j <= miss; j++) out.push({ t: prev.t + j * tf, o: prev.c, h: prev.c, l: prev.c, c: prev.c, syn: true });
      }
      out.push(c);
    }
    return out;
  }, [q.data, live?.candles, tf]);
  const bridged = useMemo(() => candles.slice(-300).filter((c) => c.syn).length, [candles]);
  const forming = live?.forming ? toCandle(live.forming) : null;

  const { overlays, panes } = useMemo(() => {
    const out = { overlays: [] as SeriesGroup[], panes: [] as SeriesGroup[] };
    if (!candles.length) return out;
    const pos = new Map(candles.map((c, i) => [c.t, i] as [number, number]));
    const add = (d: ChartData | undefined, colorOf?: (id: string) => { color: string; label: string } | undefined) => {
      if (!d) return;
      const align = (values: (number | null)[]) => { const a: (number | null)[] = new Array(candles.length).fill(null); d.candles.forEach((c, j) => { const i = pos.get(c.t); if (i !== undefined) a[i] = values[j] ?? null; }); return a; };
      d.indicators.forEach((it, k) => {
        const base = indicatorGroup(it, k, d);
        if (!base) return;
        const mark = colorOf?.(it.id);
        const g: SeriesGroup = { ...base, ...(mark ?? {}), lines: base.lines.map((l, j) => ({ ...l, values: align(it.series[j].values) })) };
        (it.panel === 'price' ? out.overlays : out.panes).push(g);
      });
    };
    if (showInd) add(ind.data);
    add(own.data, (id) => { const c = catalogItem(id.replace(/^u_/, '')); return c ? { color: c.color, label: c.name } : undefined; });
    return out;
  }, [ind.data, own.data, showInd, candles]);

  const pickable = (strategies.data ?? []).filter((s) => s.status !== 'archived' && !s.tags.includes('meta') && s.key !== 'consensus-3');
  const toolbarLeft = (
    <>
      {!lockStrategy && (
        <select className="input !w-56 !py-1 text-xs" value={sid ?? ''} onChange={(e) => setSid(e.target.value ? Number(e.target.value) : null)} title="Apply a strategy to this chart: its trades, results and indicators are drawn">
          <option value="">Strategy: none</option>{pickable.map((s) => <option key={s.id} value={s.id}>{s.name}{s.status === 'live' ? ' (live)' : ''}</option>)}
        </select>)}
      {!compact && <IndicatorMenu picked={picked} onChange={setPicked} />}
      {applied && !compact && <label className="flex cursor-pointer items-center gap-1 text-mut" title="Draw the indicators the strategy uses"><input type="checkbox" checked={showInd} onChange={(e) => setShowInd(e.target.checked)} />Strategy lines</label>}
      {(applied || runId) && mtgSteps === undefined && !compact && (
        <div className="flex items-center gap-1" title="Martingale: after a losing trade, re-enter the same direction on the next candle with a bigger stake">
          <span style={{ color: '#38bdf8' }} className="font-semibold">MTG</span>
          <div className="flex overflow-hidden rounded-lg border border-line">{[0, 1, 2, 3].map((s) => (
            <button key={s} className={`px-2 py-0.5 ${s === lSteps ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => setLSteps(s)}>{s === 0 ? 'off' : s}</button>))}</div>
          {lSteps > 0 && <label className="flex items-center gap-1 text-mut">×<input className="input !w-14 !py-0.5 text-xs" type="number" min={1} max={10} step={0.1} value={lMult} onChange={(e) => setLMult(Math.min(10, Math.max(1, Number(e.target.value) || 2.2)))} /></label>}
        </div>)}
    </>
  );

  if (!candles.length && q.isLoading) return <div className="grid animate-pulse place-items-center rounded-lg bg-panel2 text-mut" style={{ height }}>Loading {symbol}…</div>;
  if (!candles.length && q.isError) return <ErrorBox error={q.error} />;

  return (
    <PriceChart
      candles={candles} forming={forming} lastPrice={live?.lastPrice ?? null} tf={tf}
      overlays={overlays} panes={panes} zones={ind.data?.zones} swings={ind.data?.swings} markers={overlay?.markers} bands={overlay?.bands} areas={overlay?.areas} trades={q.data?.trades ?? []} payout={q.data?.payout ?? null} splitT={q.data?.split_t ?? null} mtg={q.data?.mtg ?? null}
      height={height} initialCount={initialCount} compact={compact} resetKey={`${symbol}-${tf}`} toolbarLeft={toolbarLeft}
      emptyText="Not enough stored candles for this pair and timeframe yet."
      footer={<>
        {q.data?.note && <div className="mt-1 text-xs text-warn">{q.data.note}</div>}
        {bridged > 0 && <div className="mt-1 text-[11px] text-mut">{bridged} recent candle{bridged === 1 ? ' was' : 's were'} never received (the connection to Quotex dropped) and {bridged === 1 ? 'is' : 'are'} filled with the previous candle's close (the flat grey candles). The real ones replace them by themselves when Quotex re-sends its history, for example after you switch the timeframe or the pair on the Quotex tab and back.</div>}
        {own.isError && <div className="mt-1 text-[11px] text-mut">The indicators you picked could not be drawn: {own.error instanceof Error ? own.error.message : 'not available'}.</div>}
        {ind.isError && <div className="mt-1 text-[11px] text-mut">Zones, structure and indicators are not drawn here: {ind.error instanceof Error ? ind.error.message : 'not available'}.</div>}
        {(ind.data?.runs ?? 0) > 1 && <div className="mt-1 text-[11px] text-mut">Zones, structure and indicators are worked out separately for each of the {ind.data!.runs} uninterrupted stretches of candles (they never span a data gap).</div>}
      </>}
    />
  );
}
