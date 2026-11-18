import { useEffect, useMemo, useRef, useState } from 'react';
import { prettyPair } from '../format';

export interface PairOption { symbol: string; n: number; payout: number | null }

/**
 * A dropdown to pick any number of pairs from the list of available ones: search, tick the pairs you want, "Select shown" / "Clear".
 * Nothing ticked = no pair filter (all pairs). The button says what is picked.
 */
export function PairMultiSelect({ options, value, onChange, unit = 'signals' }: { options: PairOption[]; value: string[]; onChange: (v: string[]) => void; unit?: string }) {
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    search.current?.focus();
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);

  const picked = useMemo(() => new Set(value), [value]);
  const shown = useMemo(() => {
    const q = find.trim().toLowerCase().replace(/[\s/_()-]/g, '');
    return options.filter((o) => !q || `${o.symbol}${prettyPair(o.symbol)}`.toLowerCase().replace(/[\s/_()-]/g, '').includes(q));
  }, [options, find]);
  const toggle = (s: string) => onChange(picked.has(s) ? value.filter((x) => x !== s) : [...value, s]);
  const label = value.length === 0 ? 'All pairs' : value.length === 1 ? prettyPair(value[0]) : `${value.length} pairs`;

  return (
    <div ref={root} className="relative">
      <button type="button" className={`input flex w-full items-center justify-between gap-2 text-left ${value.length ? 'border-acc' : ''}`} onClick={() => setOpen(!open)}
        title={value.length ? value.map(prettyPair).join(', ') : 'Filter by pair (pick one or several)'} aria-haspopup="listbox" aria-expanded={open}>
        <span className="truncate">{label}</span><span className="text-mut">▾</span>
      </button>
      {open && (
        <div className="fixed inset-x-2 bottom-2 z-[60] rounded-xl border border-line bg-panel p-2 shadow-2xl sm:absolute sm:inset-x-auto sm:bottom-auto sm:left-0 sm:top-full sm:z-40 sm:mt-1 sm:w-72 sm:rounded-lg sm:shadow-xl">
          <input ref={search} className="input !py-1 text-sm" placeholder="Search pairs…" value={find} onChange={(e) => setFind(e.target.value)} />
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="text-mut">{value.length ? `${value.length} selected` : 'none selected = all pairs'}</span>
            <span className="flex gap-3">
              <button type="button" className="text-acc hover:underline" onClick={() => onChange([...new Set([...value, ...shown.map((o) => o.symbol)])])}>Select shown</button>
              <button type="button" className="text-acc hover:underline" disabled={!value.length} onClick={() => onChange([])}>Clear</button>
            </span>
          </div>
          <ul className="mt-2 max-h-[min(18rem,50dvh)] overflow-y-auto overscroll-contain" role="listbox" aria-multiselectable="true">
            {shown.length === 0 && <li className="px-2 py-3 text-center text-xs text-mut">No pair matches.</li>}
            {shown.map((o) => (
              <li key={o.symbol} role="option" aria-selected={picked.has(o.symbol)}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-2.5 text-sm hover:bg-panel2 sm:py-1">
                  <input type="checkbox" checked={picked.has(o.symbol)} onChange={() => toggle(o.symbol)} />
                  <span className="flex-1 truncate">{prettyPair(o.symbol)}</span>
                  <span className="note tabular-nums" title={`${o.n.toLocaleString()} ${unit}${o.payout ? `, pays ${o.payout}%` : ''}`}>{o.n ? o.n.toLocaleString() : '–'}</span>
                </label>
              </li>))}
          </ul>
        </div>)}
    </div>
  );
}
