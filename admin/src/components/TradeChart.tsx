import { useMemo, useState } from 'react';

export type CandleRow = [number, number, number, number, number];            // t, open, high, low, close

export interface Mark {
  open_t: number | null; end_t: number | null; open_price: number | null; close_price: number | null; direction: 'CALL' | 'PUT' | null; status: string;
}

const COL = { up: '#22c55e', down: '#ef4444', warn: '#f59e0b', acc: '#8b5cf6', mut: '#8a93b2', line: '#1e2747', grid: '#18203d', fg: '#e6e8f2', panel: '#0d1328' };
const tone = (status: string): string => (status === 'win' ? COL.up : status === 'loss' ? COL.down : status === 'open' ? COL.warn : COL.mut);
const hhmm = (t: number, secs = false): string => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', ...(secs ? { second: '2-digit' } : {}) });

const decimalsOf = (x: number | null): number => {
  if (x === null) return 5;
  const s = String(x);
  const i = s.indexOf('.');
  return Math.min(6, Math.max(2, i < 0 ? 2 : s.length - i - 1));
};

/**
 * The 1-minute candles around ONE trade, with the trade drawn exactly where it happened: the shaded window from the second you entered to the second it expired,
 * the candle(s) it ran through outlined (the rest dimmed), the entry and exit price lines, a profit / loss zone between them, the direction arrow at the entry
 * and the result at the exit. `others` are the neighbouring trades of the same martingale cycle, drawn lighter.
 */
export default function TradeChart({ candles, tf, trade, others = [], signalT = null, height = 380 }: {
  candles: CandleRow[]; tf: number; trade: Mark; others?: Mark[]; signalT?: number | null; height?: number;
}) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const W = 860, H = height, L = 10, R = 82, T = 24, B = 34;
  const pw = W - L - R, ph = H - T - B;

  const g = useMemo(() => {
    if (!candles.length) return null;
    const t0 = candles[0][0], t1 = candles[candles.length - 1][0] + tf;
    const prices = candles.flatMap((c) => [c[2], c[3]]);
    for (const m of [trade, ...others]) { if (m.open_price !== null) prices.push(m.open_price); if (m.close_price !== null) prices.push(m.close_price); }
    let lo = Math.min(...prices), hi = Math.max(...prices);
    const pad = (hi - lo || Math.abs(hi) * 0.001 || 1) * 0.1;
    lo -= pad; hi += pad;
    return { t0, t1, lo, hi };
  }, [candles, tf, trade, others]);

  if (!g) {
    return (
      <div className="grid place-items-center rounded-xl border border-dashed border-line bg-panel2 p-8 text-center text-sm text-mut" style={{ minHeight: 200 }}>
        <div><div className="text-base text-fg">No stored candles around this trade</div>
          <div className="mt-1">The extension was not streaming this pair then (or the history was cleared), so there is nothing to draw. The trade details below are still correct.</div></div>
      </div>
    );
  }

  const x = (t: number) => L + ((t - g.t0) / (g.t1 - g.t0)) * pw;
  const y = (p: number) => T + (1 - (p - g.lo) / (g.hi - g.lo)) * ph;
  const slot = pw / ((g.t1 - g.t0) / tf);
  const bw = Math.max(2, Math.min(18, slot * 0.64));
  const dec = decimalsOf(trade.open_price ?? candles[candles.length - 1][4]);
  const fp = (p: number) => p.toFixed(dec);

  const start = trade.open_t ?? candles[0][0];
  const end = trade.end_t ?? start + tf;
  const col = tone(trade.status);
  const inTrade = (t: number) => t + tf > start && t < end;
  const favour = trade.open_price !== null && trade.close_price !== null && trade.direction && trade.close_price !== trade.open_price
    ? (trade.close_price > trade.open_price) === (trade.direction === 'CALL') : null;
  const zoneCol = favour === null ? COL.mut : favour ? COL.up : COL.down;

  // price grid and time ticks
  const rows = 6;
  const gridPrices = Array.from({ length: rows + 1 }, (_, k) => g.lo + ((g.hi - g.lo) * k) / rows);
  const every = Math.max(1, Math.ceil(candles.length / 9));
  const hv = hover ? candles[hover.i] : null;
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W, sy = ((e.clientY - r.top) / r.height) * H;
    if (sx < L || sx > L + pw) { setHover(null); return; }
    const i = Math.min(candles.length - 1, Math.max(0, Math.floor(((sx - L) / pw) * ((g.t1 - g.t0) / tf))));
    setHover({ i, x: sx, y: Math.min(T + ph, Math.max(T, sy)) });
  };

  const marker = (m: Mark, main: boolean) => {
    if (m.open_t === null || m.open_price === null) return null;
    const cx = x(m.open_t), cy = y(m.open_price), c = tone(m.status), call = m.direction === 'CALL';
    const ex = m.end_t !== null && m.close_price !== null ? { x: x(m.end_t), y: y(m.close_price) } : null;
    const s = main ? 1 : 0.75;
    return (
      <g key={`${m.open_t}-${m.open_price}`} opacity={main ? 1 : 0.8}>
        {ex && <line x1={cx} y1={cy} x2={ex.x} y2={ex.y} stroke={c} strokeWidth={main ? 2 : 1.5} strokeDasharray={main ? undefined : '3 3'} />}
        <circle cx={cx} cy={cy} r={5.5 * s} fill={COL.panel} stroke="#fff" strokeWidth={2} />
        <path d={call ? `M${cx},${cy + 11 * s} l${-6 * s},${9 * s} h${12 * s} z` : `M${cx},${cy - 11 * s} l${-6 * s},${-9 * s} h${12 * s} z`} fill={call ? COL.up : COL.down} stroke="#0a0e1f" strokeWidth={1} />
        {ex && (
          <g>
            <circle cx={ex.x} cy={ex.y} r={8 * s} fill={c} stroke="#0a0e1f" strokeWidth={2} />
            <text x={ex.x} y={ex.y + 4 * s} textAnchor="middle" fontSize={11 * s} fontWeight={800} fill="#0a0e1f">{m.status === 'win' ? '✓' : m.status === 'loss' ? '✕' : m.status === 'draw' ? '=' : '…'}</text>
          </g>
        )}
      </g>
    );
  };

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full select-none rounded-xl border border-line bg-[#0b1126]" role="img" aria-label="Candles around the trade with its entry and exit"
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="tw" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={col} stopOpacity="0.20" /><stop offset="1" stopColor={col} stopOpacity="0.05" /></linearGradient>
        </defs>

        {gridPrices.map((p, k) => (
          <g key={k}><line x1={L} x2={L + pw} y1={y(p)} y2={y(p)} stroke={COL.grid} /><text x={L + pw + 8} y={y(p) + 4} fontSize={10.5} fill={COL.mut}>{fp(p)}</text></g>
        ))}
        {candles.map((c, i) => (i % every === 0 ? (
          <g key={c[0]}><line x1={x(c[0] + tf / 2)} x2={x(c[0] + tf / 2)} y1={T} y2={T + ph} stroke={COL.grid} /><text x={x(c[0] + tf / 2)} y={H - 12} textAnchor="middle" fontSize={10.5} fill={COL.mut}>{hhmm(c[0])}</text></g>
        ) : null))}

        {/* the trade window: from the second you entered to the second it expired */}
        <rect x={x(start)} y={T} width={Math.max(2, x(end) - x(start))} height={ph} fill="url(#tw)" />
        <line x1={x(start)} x2={x(start)} y1={T} y2={T + ph} stroke={col} strokeWidth={1.2} strokeDasharray="4 4" opacity={0.8} />
        <line x1={x(end)} x2={x(end)} y1={T} y2={T + ph} stroke={col} strokeWidth={1.2} strokeDasharray="4 4" opacity={0.8} />
        <rect x={Math.max(L, x(start) - 1)} y={T - 17} width={Math.max(80, x(end) - x(start) + 2)} height={16} rx={4} fill={col} />
        <text x={Math.max(L, x(start) - 1) + 6} y={T - 5.5} fontSize={10} fontWeight={800} fill="#0a0e1f">{trade.status === 'open' ? 'RUNNING' : 'YOUR TRADE'}</text>

        {/* profit / loss zone between the entry and the exit price */}
        {trade.open_price !== null && trade.close_price !== null && (
          <rect x={x(start)} y={Math.min(y(trade.open_price), y(trade.close_price))} width={Math.max(2, x(end) - x(start))} height={Math.max(2, Math.abs(y(trade.open_price) - y(trade.close_price)))}
            fill={zoneCol} opacity={0.28} />
        )}

        {/* candles: the ones the trade ran through are lit and outlined, the rest dimmed */}
        {candles.map((c) => {
          const [t, o, h, l, cl] = c, up = cl >= o, cx = x(t + tf / 2), lit = inTrade(t), color = up ? COL.up : COL.down;
          const top = y(Math.max(o, cl)), bot = y(Math.min(o, cl));
          return (
            <g key={t} opacity={lit ? 1 : 0.5}>
              {lit && <rect x={cx - slot / 2 + 1} y={y(h) - 5} width={slot - 2} height={y(l) - y(h) + 10} rx={4} fill="none" stroke={col} strokeWidth={1.6} />}
              <line x1={cx} x2={cx} y1={y(h)} y2={y(l)} stroke={color} strokeWidth={1.3} />
              <rect x={cx - bw / 2} y={top} width={bw} height={Math.max(1.5, bot - top)} fill={color} rx={1} />
            </g>
          );
        })}

        {/* the signal that was on screen */}
        {signalT !== null && signalT >= g.t0 && signalT <= g.t1 && (
          <g><line x1={x(signalT)} x2={x(signalT)} y1={T} y2={T + ph} stroke={COL.acc} strokeWidth={1.2} strokeDasharray="2 4" />
            <rect x={x(signalT) - 24} y={T + ph - 17} width={48} height={15} rx={4} fill={COL.acc} /><text x={x(signalT)} y={T + ph - 6} textAnchor="middle" fontSize={10} fontWeight={800} fill="#fff">SIGNAL</text></g>
        )}

        {/* price lines */}
        {trade.open_price !== null && (
          <g><line x1={x(start)} x2={L + pw} y1={y(trade.open_price)} y2={y(trade.open_price)} stroke="#fff" strokeWidth={1} strokeDasharray="5 4" opacity={0.75} />
            <rect x={L + pw + 2} y={y(trade.open_price) - 9} width={R - 4} height={18} rx={4} fill="#fff" /><text x={L + pw + 2 + (R - 4) / 2} y={y(trade.open_price) + 4} textAnchor="middle" fontSize={10.5} fontWeight={800} fill="#0a0e1f">{fp(trade.open_price)}</text></g>
        )}
        {trade.close_price !== null && (
          <g><line x1={x(end)} x2={L + pw} y1={y(trade.close_price)} y2={y(trade.close_price)} stroke={col} strokeWidth={1} strokeDasharray="5 4" opacity={0.9} />
            <rect x={L + pw + 2} y={y(trade.close_price) - 9} width={R - 4} height={18} rx={4} fill={col} /><text x={L + pw + 2 + (R - 4) / 2} y={y(trade.close_price) + 4} textAnchor="middle" fontSize={10.5} fontWeight={800} fill="#0a0e1f">{fp(trade.close_price)}</text></g>
        )}

        {others.map((m) => marker(m, false))}
        {marker(trade, true)}
        {trade.open_t !== null && (
          <g><rect x={x(start) - 34} y={T + ph + 2} width={68} height={16} rx={4} fill="#fff" /><text x={x(start)} y={T + ph + 13.5} textAnchor="middle" fontSize={10} fontWeight={800} fill="#0a0e1f">{hhmm(start, true)}</text></g>
        )}

        {/* crosshair */}
        {hover && hv && (
          <g pointerEvents="none"><line x1={hover.x} x2={hover.x} y1={T} y2={T + ph} stroke="#fff" opacity={0.25} /><line x1={L} x2={L + pw} y1={hover.y} y2={hover.y} stroke="#fff" opacity={0.25} />
            <rect x={L + pw + 2} y={hover.y - 9} width={R - 4} height={18} rx={4} fill="#2a3358" />
            <text x={L + pw + 2 + (R - 4) / 2} y={hover.y + 4} textAnchor="middle" fontSize={10.5} fill="#fff">{fp(g.hi - ((hover.y - T) / ph) * (g.hi - g.lo))}</text></g>
        )}
      </svg>

      {hover && hv && (
        <div className="pointer-events-none absolute z-10 rounded-lg border border-line bg-bg/95 px-2.5 py-1.5 text-xs shadow-xl backdrop-blur"
          style={{ left: `${Math.min(78, (hover.x / W) * 100 + 2)}%`, top: 8 }}>
          <div className="font-semibold text-fg">{hhmm(hv[0])} <span className="font-normal text-mut">candle</span></div>
          <div className="mt-0.5 grid grid-cols-[auto_auto] gap-x-3 tabular-nums text-mut">
            <span>Open</span><b className="text-right text-fg">{fp(hv[1])}</b><span>High</span><b className="text-right text-fg">{fp(hv[2])}</b>
            <span>Low</span><b className="text-right text-fg">{fp(hv[3])}</b><span>Close</span><b className={`text-right ${hv[4] >= hv[1] ? 'text-up' : 'text-down'}`}>{fp(hv[4])}</b>
          </div>
        </div>
      )}
    </div>
  );
}
