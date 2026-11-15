import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { prettyPair } from '../format';
import { Card, ErrorBox } from './ui';
import { STEP_COLORS } from '../lib/patterns';
import { Hl, useBuilder } from './builderContext';
import { PriceChart } from './chart/PriceChart';
import type { ChartBand, ChartBox, ChartCandle, ChartMarker, ChartSwing, ChartZone, SeriesGroup } from './chart/types';

export interface IndSeries {
  id: string; type: string; label: string; panel: 'price' | 'osc' | 'none';
  series: { field: string | null; panel: string; style: 'line' | 'dash' | 'dots' | 'bars'; values: (number | null)[] }[];
  levels: number[]; range: [number, number] | null;
}
export interface ChartData {
  candles: { t: number; o: number; h: number; l: number; c: number; syn: boolean }[];
  indicators: IndSeries[];
  marks: Record<string, { hits: boolean[]; error: string | null; spans?: Record<string, [number, number, number][]> }>;
  signals: { i: number; dir: 'CALL' | 'PUT'; strength: number }[];
  note: string | null; errors: string[]; need: number; have: number; symbol: string; tf: number;
  zones?: ChartZone[]; swings?: ChartSwing[]; runs?: number;
}

/** How an indicator's server series become chart lines: the zone tracker's single "touched zone" lines are replaced by the real zone rectangles (when the server sent them),
 *  and the market-structure swing lines are coloured (red = last swing high = resistance, green = last swing low = support). */
export function indicatorGroup(d: IndSeries, k: number, data: Pick<ChartData, 'zones'>): SeriesGroup | null {
  if (!d.series.length || d.panel === 'none') return null;
  if ((d.type === 'zones' || d.type === 'engulf') && data.zones?.length) return null;
  const color = indColor(k);
  return {
    id: d.id, label: `${d.id} · ${d.label}`, color, levels: d.levels, range: d.range,
    lines: d.series.map((s) => ({ values: s.values, style: d.type === 'structure' ? 'dash' : s.style, color: d.type === 'structure' ? (s.field === 'sh' ? '#f87171' : '#4ade80') : undefined })),
  };
}

export const PALETTE = ['#f59e0b', '#38bdf8', '#e879f9', '#34d399', '#fb7185', '#a3e635', '#f97316', '#22d3ee', '#c084fc', '#facc15'];
export const indColor = (i: number) => PALETTE[i % PALETTE.length];
const UP = '#22c55e', DOWN = '#ef4444', ACC = '#8b5cf6';
const TONE: Record<Hl['tone'], string> = { call: UP, put: DOWN, clause: ACC };

function runs(hits: boolean[]): [number, number][] {
  const out: [number, number][] = [];
  let s = -1;
  hits.forEach((h, i) => { if (h && s < 0) s = i; if (!h && s >= 0) { out.push([s, i - 1]); s = -1; } });
  if (s >= 0) out.push([s, hits.length - 1]);
  return out;
}

/**
 * The builder's chart: your draft strategy's indicators, the highlighted condition (shaded columns / numbered pattern boxes) and the signal markers - drawn by the
 * shared PriceChart, so it zooms, scrolls, hides indicators and goes full screen like every other chart in the app.
 */
export function ChartView({ data, hl, groups, mainH = 300 }: { data: ChartData; hl: Hl | null; groups: Hl[]; mainH?: number }) {
  const built = useMemo(() => {
    const candles: ChartCandle[] = data.candles.map((c) => ({ t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, syn: c.syn }));
    const overlays: SeriesGroup[] = [], panes: SeriesGroup[] = [];
    data.indicators.forEach((d, k) => { const g = indicatorGroup(d, k, data); if (g) (d.panel === 'price' ? overlays : panes).push(g); });
    const markers: ChartMarker[] = data.signals.filter((s) => candles[s.i]).map((s) => ({
      t: candles[s.i].t, dir: s.dir === 'CALL' ? 1 : -1, tip: `${s.dir === 'CALL' ? '▲ CALL' : '▼ PUT'} signal (strength ${s.strength}) for the next candle`,
    }));
    const hits = hl ? data.marks[hl.key]?.hits : undefined;
    const bands: ChartBand[] = hits && hl ? runs(hits).map(([a, b]) => ({ t0: candles[a].t, t1: candles[b].t, color: TONE[hl.tone], opacity: hl.tone === 'clause' ? 0.3 : 0.28 })) : [];
    const spanMap = hl ? data.marks[hl.key]?.spans : undefined;                         // candle-pattern matches: which candles played which step
    const matches = spanMap ? Object.entries(spanMap).slice(-14) : [];
    const boxes: ChartBox[] = matches.flatMap(([, seq]) => seq.filter(([, a, b]) => candles[a] && candles[b])
      .map(([k, a, b]) => ({ t0: candles[a].t, t1: candles[b].t, color: STEP_COLORS[k % STEP_COLORS.length], label: String(k + 1) })));
    const stepAt = (i: number) => { for (const [, seq] of matches) for (const [k, a, b] of seq) if (i >= a && i <= b) return k; return -1; };
    return { candles, overlays, panes, markers, bands, boxes, stepAt };
  }, [data, hl]);

  const extra = (i: number) => {
    const st = hl?.steps ? built.stepAt(i) : -1;
    return (
      <>
        {(hl || groups.length > 0) && <div className="mt-1 border-t border-line pt-1">
          {hl && <div className={data.marks[hl.key]?.hits[i] ? 'text-up' : 'text-mut'}>{data.marks[hl.key]?.hits[i] ? '✓ ' : '✗ '}{hl.label}</div>}
          {groups.filter((g) => data.marks[g.key]?.hits[i] && g.key !== hl?.key).map((g) => <div key={g.key} style={{ color: TONE[g.tone] }}>✓ {g.label}</div>)}
        </div>}
        {hl?.steps && st >= 0 && <div className="mt-1" style={{ color: STEP_COLORS[st % STEP_COLORS.length] }}>Step {st + 1}: {hl.steps[st]}</div>}
      </>
    );
  };

  return (
    <PriceChart candles={built.candles} tf={data.tf} overlays={built.overlays} panes={built.panes} markers={built.markers} bands={built.bands} boxes={built.boxes} zones={data.zones} swings={data.swings}
      height={mainH} initialCount={built.candles.length} resetKey={`${data.symbol}-${data.tf}`} tooltipExtra={extra} />
  );
}

// ------------------------------------------------------------------ data hook + the card shown at the top of the builder
export function useChartQuery(spec: unknown, exprs: Record<string, string>, pats: Record<string, unknown>, pair: string, tf: number, count: number) {
  const [debounced, setDebounced] = useState({ spec, exprs, pats });
  useEffect(() => { const t = setTimeout(() => setDebounced({ spec, exprs, pats }), 450); return () => clearTimeout(t); }, [spec, exprs, pats]);
  return useQuery({
    queryKey: ['strategy-chart', pair, tf, count, JSON.stringify(debounced)], enabled: !!pair, placeholderData: keepPreviousData, staleTime: 5000, retry: false,
    queryFn: () => api<ChartData>('/api/strategies/chart', { body: { spec: debounced.spec, symbol: pair, tf, count, exprs: debounced.exprs, patterns: debounced.pats, marks: true } }),
  });
}

export function BuilderChart({ q, symbols, pair, setPair, tf, setTf, count, setCount }: {
  q: ReturnType<typeof useChartQuery>; symbols: string[]; pair: string; setPair: (s: string) => void; tf: number; setTf: (n: number) => void; count: number; setCount: (n: number) => void;
}) {
  const { hl, pinned, unpin, groups } = useBuilder();
  const [tall, setTall] = useState(false);
  const [sticky, setSticky] = useState(false);
  const d = q.data;
  const mark = hl && d ? d.marks[hl.key] : undefined;
  const hitCount = mark ? mark.hits.filter(Boolean).length : 0;

  return (
    <div className={sticky ? 'sticky top-0 z-30' : ''}>
      <Card title="Chart · your indicators and conditions" right={
        <div className="flex flex-wrap items-center gap-2 normal-case tracking-normal">
          <select className="input !w-44 !py-1 text-xs" value={pair} onChange={(e) => setPair(e.target.value)}>{symbols.map((s) => <option key={s} value={s}>{prettyPair(s)}</option>)}</select>
          <select className="input !w-16 !py-1 text-xs" value={tf} onChange={(e) => setTf(Number(e.target.value))}><option value={60}>1m</option><option value={300}>5m</option><option value={900}>15m</option></select>
          <select className="input !w-32 !py-1 text-xs" value={count} onChange={(e) => setCount(Number(e.target.value))}>{[60, 120, 200, 300, 400].map((c) => <option key={c} value={c}>{c} candles</option>)}</select>
          <button className="btn btn-sm" onClick={() => setTall(!tall)}>{tall ? 'Shorter' : 'Taller'}</button>
          <button className={`btn btn-sm ${sticky ? 'border-acc text-[#c4b5fd]' : ''}`} onClick={() => setSticky(!sticky)} title="Keep the chart in view while you scroll through the form">{sticky ? 'Pinned' : 'Pin to top'}</button>
        </div>}>
        {/* what is highlighted */}
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {d?.indicators.filter((i) => i.panel === 'none').map((i) => <span key={i.id} className="text-mut">{i.id} · {i.label} (not drawn)</span>)}
          <span className="inline-flex items-center gap-1 text-up">▲ CALL signal</span><span className="inline-flex items-center gap-1 text-down">▼ PUT signal</span>
          {hl && d && (
            <span className="ml-auto inline-flex items-center gap-2 rounded-lg border px-2 py-1" style={{ borderColor: TONE[hl.tone], color: TONE[hl.tone] }}>
              <span className="inline-block h-3 w-3 rounded-sm" style={{ background: TONE[hl.tone], opacity: 0.5 }} />
              {mark?.error ? <span className="text-down">{hl.label}: {mark.error}</span> : <span>{hl.label} · {hl.steps ? 'found' : 'true on'} <b>{hitCount}</b>{hl.steps ? '×' : ''} of {d.candles.length} candles</span>}
              {hl.steps && hl.steps.length > 0 && <span className="hidden items-center gap-2 md:inline-flex">{hl.steps.map((st, k) => <span key={k} className="inline-flex items-center gap-1"><span className="grid h-4 w-4 place-items-center rounded-full text-[10px] font-bold text-black" style={{ background: STEP_COLORS[k % STEP_COLORS.length] }}>{k + 1}</span><span className="text-mut">{st}</span></span>)}</span>}
              {pinned && <button className="underline" onClick={unpin}>unpin</button>}
            </span>
          )}
        </div>

        {!pair ? <div className="p-8 text-center text-mut">No stored candles yet. Keep the Quotex tab open with the extension on and a pair will appear here.</div>
          : q.isError && !d ? <ErrorBox error={q.error} />
          : !d ? <div className="grid h-60 animate-pulse place-items-center rounded-lg bg-panel2 text-mut">Loading chart…</div>
          : (
            <div className={q.isFetching ? 'opacity-90' : ''}>
              <ChartView data={d} hl={hl} groups={groups} mainH={tall ? 420 : 280} />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-mut">
                <span>{prettyPair(d.symbol)} · {d.candles.length} candles · hover for values · {hl ? 'shaded columns = where the highlighted condition is true' : 'hover or click the eye on any condition below to highlight it here'}</span>
                {q.isFetching && <span className="text-[#c4b5fd]">updating…</span>}
              </div>
              {d.note && <div className="mt-1 text-xs text-warn">{d.note}</div>}
              {d.errors.map((e) => <div key={e} className="mt-1 text-xs text-warn">{e}</div>)}
              {d.have < d.need && <div className="mt-1 text-xs text-warn">Only {d.have} candles are stored for this pair; the strategy needs {d.need} before it signals (markers start after that).</div>}
            </div>
          )}
        {q.isError && d && <div className="mt-2 text-xs text-down">Chart update failed: {q.error instanceof Error ? q.error.message : 'error'}</div>}
      </Card>
    </div>
  );
}
