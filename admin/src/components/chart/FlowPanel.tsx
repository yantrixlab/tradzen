import { usePref } from '../../prefs';
import type { FlowReading } from './types';

const UP = '#22c55e', DOWN = '#ef4444', AMBER = '#fbbf24';
const price = (p: number) => p.toFixed(p >= 1000 ? 1 : p >= 100 ? 2 : p >= 10 ? 3 : p >= 1 ? 4 : 5);

/** One reading in a bar from the middle: green to the right = buyers, red to the left = sellers. */
function Meter({ name, value, weight }: { name: string; value: number; weight: number }) {
  const w = Math.min(50, Math.abs(value) * 50);
  return (
    <div className="min-w-0" title={`${name}: ${value > 0 ? 'favours buyers' : value < 0 ? 'favours sellers' : 'neutral'} (${Math.round(value * 100)}); counts for ${Math.round(weight * 100)}% of the score`}>
      <div className="flex items-baseline justify-between gap-2 text-[10.5px]"><span className="truncate text-mut">{name}</span><span className="tabular-nums" style={{ color: value > 0.05 ? UP : value < -0.05 ? DOWN : undefined }}>{value > 0 ? '+' : ''}{Math.round(value * 100)}</span></div>
      <div className="relative mt-0.5 h-1.5 rounded-full bg-panel2"><span className="absolute inset-y-0 left-1/2 w-px bg-line" />
        <span className="absolute inset-y-0 rounded-full" style={{ background: value >= 0 ? UP : DOWN, width: `${w}%`, left: value >= 0 ? '50%' : `${50 - w}%` }} /></div>
    </div>
  );
}

/**
 * Who is in control right now - read from the candles (the server's market read): buyers vs sellers as a bar and a score, the structure behind it (trend, last break of
 * structure and whether it is confirmed, the level that protects the trend), the six readings it is made of, and in words why. It describes the present; the footer says
 * how it has done as a forecast on the stored candles, so nobody mistakes it for one.
 */
export function FlowPanel({ flow, tf }: { flow: FlowReading; tf: number }) {
  const [open, setOpen] = usePref('chart.flow-open', true);
  const col = flow.side > 0 ? UP : flow.side < 0 ? DOWN : '#94a3b8';
  const lb = flow.last_break, acc = flow.accuracy;
  const mins = (c: number) => { const s = c * tf; return s >= 120 ? `${Math.round(s / 60)} min` : `${s} s`; };
  const trendText = flow.trend > 0 ? '▲ UP · higher highs, higher lows' : flow.trend < 0 ? '▼ DOWN · lower highs, lower lows' : '◆ no clear trend yet';
  return (
    <section className="mb-2 rounded-xl border border-line bg-panel2/40 p-3" aria-label="Who is in control">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-mut">Who is in control</div>
          <div className="text-base font-extrabold leading-tight sm:text-lg" style={{ color: col }}>{flow.headline}</div>
          <div className="mt-0.5 text-xs text-mut">
            Structure <b style={{ color: flow.trend > 0 ? UP : flow.trend < 0 ? DOWN : undefined }}>{trendText}</b>
            {flow.protected !== null && <> · protected by <b className="text-fg tabular-nums">{price(flow.protected)}</b> (a close {flow.trend > 0 ? 'below' : 'above'} it changes direction)</>}
          </div>
          {lb && <div className="mt-0.5 text-xs text-mut">Last break: <b style={{ color: lb.type === 'CHoCH' ? AMBER : lb.dir > 0 ? UP : DOWN }}>{lb.type === 'CHoCH' ? 'CHoCH (change)' : 'BOS (continuation)'} {lb.dir > 0 ? '▲' : '▼'}</b> {lb.strong ? <b className="text-up">✓ confirmed</b> : <b className="text-warn">not confirmed</b>} · {lb.ago === 0 ? 'on the newest candle' : `${lb.ago} candle${lb.ago === 1 ? '' : 's'} (${mins(lb.ago)}) ago`}</div>}
        </div>
        <div className="w-full min-w-[220px] sm:w-72">
          <div className="flex justify-between text-[11px] font-bold"><span style={{ color: UP }}>BUYERS {Math.round(flow.buyers)}%</span><span style={{ color: DOWN }}>{Math.round(flow.sellers)}% SELLERS</span></div>
          <div className="relative mt-1 flex h-3 overflow-hidden rounded-full bg-panel2" role="img" aria-label={`Buyers ${Math.round(flow.buyers)} percent, sellers ${Math.round(flow.sellers)} percent`}>
            <span style={{ width: `${flow.buyers}%`, background: UP }} className="transition-all" /><span style={{ width: `${flow.sellers}%`, background: DOWN }} className="transition-all" />
            <span className="absolute inset-y-0 left-1/2 w-0.5 bg-bg/80" /></div>
          <div className="mt-1 text-right text-[10.5px] text-mut">score <b className="tabular-nums" style={{ color: col }}>{flow.score > 0 ? '+' : ''}{flow.score.toFixed(0)}</b> of ±100 · {flow.tier}{flow.pullback ? ' · pull-back' : ''}{flow.stretched ? ' · stretched' : ''}</div>
        </div>
      </div>
      <button type="button" className="mt-2 text-[11px] font-semibold text-mut hover:text-fg" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '▾ Hide the details' : '▸ Why? The readings behind it'}</button>
      {open && (
        <div className="mt-2 grid gap-3 lg:grid-cols-[1fr_1.1fr]">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3 lg:grid-cols-2">{flow.components.map((c) => <Meter key={c.key} name={c.name} value={c.value} weight={c.weight} />)}</div>
          <div>
            <ul className="space-y-1 text-xs text-fg/90">{flow.reasons.map((r, i) => <li key={i} className="flex gap-1.5"><span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: i === 0 ? col : '#64748b' }} /><span>{r}</span></li>)}</ul>
            {acc && (
              <p className="mt-2 rounded-lg border border-line/70 bg-panel2/60 p-2 text-[10.5px] leading-snug text-mut">
                <b className="text-fg">A reading, not a forecast.</b> Measured on {acc.sample} (never used to tune it): price went the way the reading favoured {acc.next_candle.toFixed(0)}% of the time on the next candle and {acc.after_10.toFixed(0)}% ten candles on (50% = a coin flip).
                What does hold up: a breakout confirmed with a strong close ✓ ran on {acc.strong_break_follow.toFixed(0)}% of the time, an unconfirmed one only {acc.weak_break_follow.toFixed(0)}%.
              </p>)}
          </div>
        </div>)}
    </section>
  );
}
