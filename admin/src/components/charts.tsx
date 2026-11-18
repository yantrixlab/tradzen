import { useMemo } from 'react';
import { Candle } from '../api';
import { dateTime, num, pct } from '../format';
import { PriceChart } from './chart/PriceChart';
import { useBoxWidth } from '../lib/useBoxWidth';
import type { ChartBand, ChartMarker } from './chart/types';

/** Cumulative profit curve (units of stake). `marker` draws a dashed line where the validation period starts. */
export function EquityChart({ points, marker, height = 180 }: { points: [number, number][]; marker?: number | null; height?: number }) {
  const [box, bw] = useBoxWidth(800);
  if (points.length < 2) return <div className="note p-4 text-center">Not enough settled trades for a curve yet.</div>;
  const W = Math.min(800, bw), H = height, pad = 28;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const sx = (x: number) => pad + ((x - x0) / (x1 - x0 || 1)) * (W - pad * 1.5);
  const sy = (y: number) => H - pad - ((y - y0) / (y1 - y0 || 1)) * (H - pad * 1.6);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(' ');
  const last = ys[ys.length - 1];
  const col = last >= 0 ? '#22c55e' : '#ef4444';
  return (
    <div ref={box}><svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="equity curve">
      <line x1={pad} x2={W - 8} y1={sy(0)} y2={sy(0)} stroke="#2a3358" strokeDasharray="4 4" />
      {marker && marker > x0 && marker < x1 && (
        <g><line x1={sx(marker)} x2={sx(marker)} y1={8} y2={H - pad} stroke="#8b5cf6" strokeDasharray="4 4" />
          <text x={sx(marker) + 4} y={18} fill="#c4b5fd" fontSize="11">validation period →</text></g>
      )}
      <path d={`${d} L${sx(x1)},${sy(0)} L${sx(x0)},${sy(0)} Z`} fill={col} opacity="0.08" />
      <path d={d} fill="none" stroke={col} strokeWidth="2" />
      <text x={pad} y={H - 8} fill="#8a93b2" fontSize="11">{dateTime(x0)}</text>
      <text x={W - 8} y={H - 8} fill="#8a93b2" fontSize="11" textAnchor="end">{dateTime(x1)}</text>
      <text x={W - 8} y={sy(last) - 6} fill={col} fontSize="12" fontWeight="700" textAnchor="end">{last >= 0 ? '+' : ''}{num(last, 2)} units</text>
      <text x={4} y={sy(y1) + 4} fill="#8a93b2" fontSize="10">{num(y1, 1)}</text>
      <text x={4} y={sy(y0) + 4} fill="#8a93b2" fontSize="10">{num(y0, 1)}</text>
    </svg></div>
  );
}

/** The candles around one signal: the signal candle and the settling candle are shaded and the bet is marked. Same chart as everywhere (zoom, scroll, full screen). */
export function CandleChart({ candles, signalT, targetT, direction, height = 240 }: { candles: Candle[]; signalT?: number; targetT?: number; direction?: string; height?: number }) {
  const data = useMemo(() => candles.map((c) => ({ t: c.t, o: c.o, h: c.h, l: c.l, c: c.c })), [candles]);
  const bands: ChartBand[] = [];
  if (signalT !== undefined) bands.push({ t0: signalT, t1: signalT, color: '#8b5cf6', opacity: 0.14, label: 'signal' });
  if (targetT !== undefined) bands.push({ t0: targetT, t1: targetT, color: direction === 'CALL' ? '#22c55e' : '#ef4444', opacity: 0.14, label: direction === 'CALL' ? '▲ bet' : '▼ bet' });
  const markers: ChartMarker[] = signalT !== undefined && (direction === 'CALL' || direction === 'PUT') ? [{ t: signalT, dir: direction === 'CALL' ? 1 : -1, tip: `${direction === 'CALL' ? '▲ CALL' : '▼ PUT'} signal` }] : [];
  return <PriceChart candles={data} bands={bands} markers={markers} height={height} compact initialCount={Math.max(20, data.length)} resetKey={`${signalT ?? ''}`} emptyText="No stored candles around this signal." />;
}

export interface BarRow { label: string; n: number; acc: number | null; lo: number | null; hi: number | null; sub?: string }

/** Horizontal accuracy bars with 95 % interval whiskers, a baseline marker and the break-even line. */
export function AccuracyBars({ rows, baseline, breakeven }: { rows: BarRow[]; baseline?: number | null; breakeven?: number | null }) {
  const sx = (v: number) => `${Math.max(0, Math.min(1, (v - 0.2) / 0.6)) * 100}%`;
  return (
    <div className="space-y-1.5">
      <div className="relative ml-[168px] mr-[88px] h-4 text-[10px] text-mut">
        {[0.3, 0.4, 0.5, 0.6, 0.7].map((v) => <span key={v} className="absolute -translate-x-1/2" style={{ left: sx(v) }}>{Math.round(v * 100)}%</span>)}
      </div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 text-xs">
          <div className="w-40 shrink-0 truncate text-right" title={r.label}>{r.label}</div>
          <div className="relative h-5 flex-1 rounded bg-panel2">
            {r.acc !== null && (
              <>
                <div className="absolute inset-y-1 left-0 rounded" style={{ width: sx(r.acc), background: baseline != null && r.acc < baseline ? '#ef444466' : '#22c55e55' }} />
                {r.lo !== null && r.hi !== null && <div className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-fg/70" style={{ left: sx(r.lo), width: `calc(${sx(r.hi)} - ${sx(r.lo)})` }} />}
              </>
            )}
            {baseline != null && <div className="absolute inset-y-0 w-px bg-acc" style={{ left: sx(baseline) }} title={`baseline ${pct(baseline)}`} />}
            {breakeven != null && <div className="absolute inset-y-0 w-px border-l border-dashed border-warn" style={{ left: sx(breakeven) }} title={`break-even ${pct(breakeven)}`} />}
          </div>
          <div className="w-20 shrink-0 tabular-nums text-mut">{pct(r.acc)} <span className="text-[10px]">n={r.n}</span></div>
        </div>
      ))}
      <div className="note pl-[176px]"><span className="text-acc">│</span> baseline &nbsp; <span className="text-warn">┆</span> break-even &nbsp; whiskers = 95 % interval</div>
    </div>
  );
}
