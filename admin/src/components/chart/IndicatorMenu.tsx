import { useEffect, useMemo, useRef, useState } from 'react';

/** The ready-made indicators a person can put on any chart. `type` and `params` are the server's indicator names (indreg.py); the chart computes them for the pair on screen. */
export interface CatalogItem { key: string; name: string; type: string; params: Record<string, number>; color: string; pane: boolean; group: string; hint: string }

const C = (key: string, name: string, type: string, params: Record<string, number>, color: string, pane: boolean, group: string, hint: string): CatalogItem => ({ key, name, type, params, color, pane, group, hint });

export const CATALOG: CatalogItem[] = [
  C('ema9', 'EMA 9', 'ema', { n: 9 }, '#fbbf24', false, 'Moving averages', 'Fast exponential average of the close'),
  C('ema21', 'EMA 21', 'ema', { n: 21 }, '#38bdf8', false, 'Moving averages', 'Exponential average, 21 candles'),
  C('ema50', 'EMA 50', 'ema', { n: 50 }, '#e879f9', false, 'Moving averages', 'Exponential average, 50 candles'),
  C('ema200', 'EMA 200', 'ema', { n: 200 }, '#e5e7eb', false, 'Moving averages', 'The long-term trend line'),
  C('sma20', 'SMA 20', 'sma', { n: 20 }, '#34d399', false, 'Moving averages', 'Simple average, 20 candles'),
  C('sma50', 'SMA 50', 'sma', { n: 50 }, '#fb923c', false, 'Moving averages', 'Simple average, 50 candles'),
  C('sma200', 'SMA 200', 'sma', { n: 200 }, '#a78bfa', false, 'Moving averages', 'Simple average, 200 candles'),
  C('boll', 'Bollinger Bands (20, 2)', 'bollinger', { n: 20, k: 2 }, '#60a5fa', false, 'Bands and trend', 'Average ± 2 standard deviations: stretched price touches the outer bands'),
  C('donch', 'Donchian Channel (20)', 'donchian', { n: 20 }, '#2dd4bf', false, 'Bands and trend', 'Highest high and lowest low of the last 20 candles'),
  C('super', 'Supertrend (10, 3)', 'supertrend', { n: 10, mult: 3 }, '#4ade80', false, 'Bands and trend', 'ATR trailing line that flips with the trend'),
  C('psar', 'Parabolic SAR', 'psar', { step: 0.02, maximum: 0.2 }, '#f472b6', false, 'Bands and trend', 'Dots that trail price and flip when the trend turns'),
  C('rsi', 'RSI (14)', 'rsi', { n: 14 }, '#c084fc', true, 'Momentum', 'Relative strength, 0–100: above 70 stretched up, below 30 stretched down'),
  C('macd', 'MACD (12, 26, 9)', 'macd', { fast: 12, slow: 26, signal: 9 }, '#38bdf8', true, 'Momentum', 'Difference of two averages, its signal line and histogram'),
  C('stoch', 'Stochastic (14, 3, 3)', 'stoch', { k: 14, smooth: 3, d: 3 }, '#fbbf24', true, 'Momentum', 'Where the close sits in the recent range, 0–100'),
  C('cci', 'CCI (20)', 'cci', { n: 20 }, '#fb7185', true, 'Momentum', 'Commodity channel index: beyond ±100 is stretched'),
  C('will', 'Williams %R (14)', 'williams', { n: 14 }, '#2dd4bf', true, 'Momentum', 'Like stochastic, from −100 to 0'),
  C('roc', 'Rate of change (10)', 'roc', { n: 10 }, '#a3e635', true, 'Momentum', 'Percent change over 10 candles'),
  C('adx', 'ADX (14)', 'adx', { n: 14 }, '#f97316', true, 'Strength', 'Trend strength: above 25 is trending, with the +DI / −DI lines'),
  C('atr', 'ATR (14)', 'atr', { n: 14 }, '#94a3b8', true, 'Strength', 'Average true range: how big the candles are'),
];
export const catalogItem = (key: string) => CATALOG.find((c) => c.key === key);

/** A searchable list of indicators to add to the chart; the choice is saved on the account by the caller. */
export function IndicatorMenu({ picked, onChange }: { picked: string[]; onChange: (keys: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const groups = useMemo(() => {
    const m = new Map<string, CatalogItem[]>();
    const s = q.trim().toLowerCase();
    for (const c of CATALOG) if (!s || c.name.toLowerCase().includes(s) || c.group.toLowerCase().includes(s) || c.hint.toLowerCase().includes(s)) m.set(c.group, [...(m.get(c.group) ?? []), c]);
    return [...m.entries()];
  }, [q]);
  const toggle = (k: string) => onChange(picked.includes(k) ? picked.filter((p) => p !== k) : [...picked, k]);
  return (
    <div ref={box} className="relative">
      <button type="button" className={`btn btn-sm ${picked.length ? '!border-acc/70 !bg-acc/15' : ''}`} aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)} title="Add indicators to the chart: moving averages, Bollinger, RSI, MACD ...">
        <span className="font-serif italic">ƒx</span> Indicators{picked.length > 0 && <span className="rounded-full bg-acc px-1.5 text-[10px] font-bold leading-4 text-white">{picked.length}</span>}
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1.5 w-[320px] max-w-[88vw] overflow-hidden rounded-xl border border-line bg-panel shadow-2xl">
          <div className="flex items-center gap-2 border-b border-line p-2">
            <input autoFocus className="input !py-1 text-xs" placeholder="Search indicators…" value={q} onChange={(e) => setQ(e.target.value)} />
            {picked.length > 0 && <button type="button" className="btn btn-sm whitespace-nowrap" onClick={() => onChange([])}>Clear</button>}
          </div>
          <div className="max-h-[340px] overflow-y-auto p-1.5">
            {groups.length === 0 && <div className="p-4 text-center text-xs text-mut">No indicator matches “{q}”.</div>}
            {groups.map(([g, items]) => (
              <div key={g} className="mb-1.5">
                <div className="px-2 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wider text-mut">{g}</div>
                {items.map((c) => {
                  const on = picked.includes(c.key);
                  return (
                    <button key={c.key} type="button" onClick={() => toggle(c.key)} title={c.hint}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${on ? 'bg-acc/15 text-fg' : 'text-mut hover:bg-panel2 hover:text-fg'}`}>
                      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: c.color }} />
                      <span className="flex-1 font-medium">{c.name}</span>
                      <span className="text-[10px] uppercase tracking-wide text-mut">{c.pane ? 'pane' : 'on price'}</span>
                      <span className={`grid h-4 w-4 place-items-center rounded border text-[10px] ${on ? 'border-acc bg-acc text-white' : 'border-line'}`}>{on ? '✓' : ''}</span>
                    </button>);
                })}
              </div>))}
          </div>
          <div className="border-t border-line px-3 py-1.5 text-[10px] text-mut">Your choice is saved on your account. Tap a name on the chart to hide a line without removing it.</div>
        </div>)}
    </div>
  );
}
