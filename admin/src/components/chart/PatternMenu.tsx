import { useEffect, useMemo, useRef, useState } from 'react';
import { BIAS_COLOR, BIAS_WORD, PATTERNS, SECTIONS, type Bias, type Group, type PatternDef } from '../../lib/chartPatterns';

export interface PatternToast { key: string; title: string; text: string; bias: Bias }

const TABS: { group: Group; label: string; hint: string }[] = [
  { group: 'candle', label: 'Candles', hint: 'One to five candles in a row' },
  { group: 'chart', label: 'Chart patterns', hint: 'Shapes made by the swing highs and lows' },
];
const FILTERS: { v: Bias | 'all'; label: string }[] = [{ v: 'all', label: 'All' }, { v: 'bull', label: 'Bullish' }, { v: 'bear', label: 'Bearish' }, { v: 'neutral', label: 'Neutral' }];

/** A small drawing of the pattern for its grid card. */
function Sketch({ def }: { def: PatternDef }) {
  const W = 72, H = 52, pad = 6;
  const col = BIAS_COLOR[def.bias];
  const sy = (v: number) => H - pad - (v / 10) * (H - 2 * pad);
  if (def.sketch.candles) {
    const cs = def.sketch.candles, gap = (W - 2 * pad) / cs.length, bw = Math.min(11, gap * 0.62);
    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="h-[52px] w-[72px]" aria-hidden="true">
        {cs.map(([o, c, h, l], k) => {
          const cx = pad + gap * (k + 0.5), up = c > o, flat = Math.abs(c - o) < 0.15, color = flat ? '#94a3b8' : up ? '#22c55e' : '#ef4444';
          return <g key={k}><line x1={cx} x2={cx} y1={sy(h)} y2={sy(l)} stroke={color} strokeWidth={1.4} /><rect x={cx - bw / 2} y={sy(Math.max(o, c))} width={bw} height={Math.max(1.6, sy(Math.min(o, c)) - sy(Math.max(o, c)))} rx={1} fill={color} /></g>;
        })}
      </svg>);
  }
  const sx = (v: number) => pad + (v / 8) * (W - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[52px] w-[72px]" aria-hidden="true">
      {(def.sketch.guides ?? []).map(([x0, y0, x1, y1], k) => <line key={k} x1={sx(x0)} y1={sy(y0)} x2={sx(x1)} y2={sy(y1)} stroke={col} strokeWidth={1.4} strokeDasharray="3 2" />)}
      <polyline points={(def.sketch.path ?? []).map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')} fill="none" stroke="#cbd5e1" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </svg>);
}

function Card({ def, on, found, onToggle }: { def: PatternDef; on: boolean; found: number; onToggle: () => void }) {
  const col = BIAS_COLOR[def.bias];
  return (
    <button type="button" role="checkbox" aria-checked={on} onClick={onToggle} title={def.about}
      className={`group relative flex min-h-[132px] flex-col items-center rounded-xl border p-2.5 text-center transition ${on ? 'border-acc bg-acc/15 shadow-[0_0_0_1px_rgba(124,92,255,.5)]' : 'border-line bg-panel2/60 hover:border-mut/60 hover:bg-panel2'}`}>
      <span className={`absolute right-2 top-2 grid h-4 w-4 place-items-center rounded border text-[10px] ${on ? 'border-acc bg-acc text-white' : 'border-line text-transparent'}`} aria-hidden="true">✓</span>
      <span className="mt-1 grid place-items-center rounded-lg bg-bg/60 px-1 py-0.5"><Sketch def={def} /></span>
      <span className="mt-1.5 text-xs font-semibold leading-tight">{def.name}</span>
      <span className="mt-1 flex flex-wrap items-center justify-center gap-1">
        <span className="rounded-full px-1.5 text-[10px] font-bold leading-4" style={{ background: col + '26', color: col }}>{BIAS_WORD[def.bias]}</span>
        {found > 0 && <span className="rounded-full bg-sky-400/15 px-1.5 text-[10px] font-semibold leading-4 text-sky-300" title="Times it was found on the candles of this chart">{found} on chart</span>}
      </span>
    </button>);
}

function Switch({ on, set, label, hint }: { on: boolean; set: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => set(!on)} title={hint} className="flex min-h-[40px] items-center gap-2 text-left text-sm">
      <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${on ? 'bg-acc' : 'bg-line'}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} /></span>
      <span className={on ? 'font-semibold' : 'text-mut'}>{label}</span>
    </button>);
}

export interface PatternMenuProps {
  picked: string[]; onChange: (ids: string[]) => void;
  alerts: boolean; onAlerts: (v: boolean) => void; sound: boolean; onSound: (v: boolean) => void;
  found: Map<string, number>; canAlert: boolean;
  toasts: PatternToast[]; onDismiss: (key: string) => void;
}

/** The "Patterns" button of the chart and its dialog: two tabs (Candles, Chart patterns), a grid of selectable patterns, and the alert switch. */
export function PatternMenu({ picked, onChange, alerts, onAlerts, sound, onSound, found, canAlert, toasts, onDismiss }: PatternMenuProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Group>('candle');
  const [q, setQ] = useState('');
  const [bias, setBias] = useState<Bias | 'all'>('all');
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const set = useMemo(() => new Set(picked), [picked]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } };
    document.addEventListener('keydown', onKey, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.current?.focus();
    const btn = opener.current;
    return () => { document.removeEventListener('keydown', onKey, true); document.body.style.overflow = prev; btn?.focus(); };
  }, [open]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return PATTERNS.filter((p) => p.group === tab && (bias === 'all' || p.bias === bias) && (!s || p.name.toLowerCase().includes(s) || p.about.toLowerCase().includes(s) || p.short.toLowerCase().includes(s)));
  }, [tab, q, bias]);
  const toggle = (id: string) => onChange(set.has(id) ? picked.filter((p) => p !== id) : [...picked, id]);
  const countIn = (g: Group) => picked.filter((id) => PATTERNS.find((p) => p.id === id)?.group === g).length;
  const addAll = () => onChange([...new Set([...picked, ...shown.map((p) => p.id)])]);
  const clearTab = () => onChange(picked.filter((id) => PATTERNS.find((p) => p.id === id)?.group !== tab));
  const turnAlerts = (v: boolean) => {
    onAlerts(v);
    if (v) { try { if (typeof Notification !== 'undefined' && Notification.permission === 'default') void Notification.requestPermission(); } catch { /* not supported */ } }
  };

  return (
    <>
      <button ref={opener} type="button" className={`btn btn-sm ${picked.length ? '!border-acc/70 !bg-acc/15' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}
        title="Candlestick and chart patterns: pick the ones to highlight on the chart, and switch alerts on">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 4v16M6 8h0M12 7v12M18 3v14" /><rect x="4" y="9" width="4" height="6" rx="1" /><rect x="10" y="10" width="4" height="6" rx="1" /><rect x="16" y="6" width="4" height="7" rx="1" /></svg>
        Patterns{picked.length > 0 && <span className="rounded-full bg-acc px-1.5 text-[10px] font-bold leading-4 text-white">{picked.length}</span>}
        {alerts && picked.length > 0 && <span className="text-[11px]" title="Alerts are on" aria-label="alerts on">🔔</span>}
      </button>

      {open && (
        <div className="fixed inset-0 z-[300] grid place-items-end bg-black/60 p-0 backdrop-blur-[2px] sm:place-items-center sm:p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div ref={dialog} role="dialog" aria-modal="true" aria-label="Candlestick and chart patterns" tabIndex={-1}
            className="flex max-h-[94dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-panel shadow-2xl outline-none sm:max-h-[88dvh] sm:max-w-5xl sm:rounded-2xl">
            <div className="flex items-start gap-3 border-b border-line px-4 py-3">
              <div className="min-w-0 flex-1">
                <h2 className="text-base font-bold">Patterns</h2>
                <p className="note">Pick the patterns to highlight on the chart. A pattern describes a shape - it is not a trade signal.</p>
              </div>
              <button type="button" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-mut hover:bg-panel2 hover:text-fg" aria-label="Close" onClick={() => setOpen(false)}>✕</button>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
              <div role="tablist" aria-label="Pattern type" className="flex gap-1">
                {TABS.map((t) => (
                  <button key={t.group} type="button" role="tab" aria-selected={tab === t.group} title={t.hint} onClick={() => setTab(t.group)}
                    className={`min-h-[36px] rounded-lg px-3 text-sm font-semibold transition ${tab === t.group ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`}>
                    {t.label}{countIn(t.group) > 0 && <span className={`ml-1.5 rounded-full px-1.5 text-[10px] font-bold leading-4 ${tab === t.group ? 'bg-white/25' : 'bg-acc/30 text-fg'}`}>{countIn(t.group)}</span>}
                  </button>))}
              </div>
              <input className="input !w-44 !py-1.5 text-xs max-sm:!w-full max-sm:order-last" placeholder="Search patterns…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search patterns" />
              <div className="flex gap-1" role="group" aria-label="Direction">
                {FILTERS.map((f) => <button key={f.v} type="button" aria-pressed={bias === f.v} onClick={() => setBias(f.v)} className={`btn btn-sm ${bias === f.v ? 'btn-on' : ''}`}>{f.label}</button>)}
              </div>
              <div className="ml-auto flex gap-1">
                <button type="button" className="btn btn-sm" onClick={addAll} disabled={!shown.length} title="Select every pattern shown here">Select shown</button>
                <button type="button" className="btn btn-sm" onClick={clearTab} disabled={!countIn(tab)} title="Unselect every pattern on this tab">Clear tab</button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">
              {shown.length === 0 && <div className="p-8 text-center text-sm text-mut">No pattern matches “{q}”.</div>}
              {SECTIONS[tab].map((sec) => {
                const items = shown.filter((p) => p.section === sec);
                if (!items.length) return null;
                return (
                  <section key={sec} className="mb-4">
                    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-mut">{sec}</h3>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                      {items.map((p) => <Card key={p.id} def={p} on={set.has(p.id)} found={found.get(p.id) ?? 0} onToggle={() => toggle(p.id)} />)}
                    </div>
                  </section>);
              })}
            </div>

            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-4 py-2.5">
              <Switch on={alerts} set={turnAlerts} label="Alert me when a selected pattern forms" hint="A message (and a sound) when a picked pattern is completed by the newest closed candle of a live chart" />
              {alerts && <label className="flex min-h-[40px] cursor-pointer items-center gap-1.5 text-sm text-mut"><input type="checkbox" checked={sound} onChange={(e) => onSound(e.target.checked)} /> Sound</label>}
              <span className="note min-w-0 flex-1 basis-56">
                {!canAlert ? 'Alerts work on live charts (Markets, Live signal).' : alerts && picked.length === 0 ? 'Pick at least one pattern to be alerted about.' : alerts ? 'Alerts show on this page while it is open; a desktop notification too when the tab is in the background.' : 'Off: patterns are only highlighted.'}
              </span>
              <button type="button" className="btn ml-auto" onClick={() => setOpen(false)}>Done{picked.length ? ` · ${picked.length} selected` : ''}</button>
            </div>
          </div>
        </div>)}

      {toasts.length > 0 && (
        <div className="pointer-events-none fixed bottom-4 right-4 z-[310] flex w-[min(92vw,22rem)] flex-col gap-2" role="status" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.key} className="pointer-events-auto flex items-start gap-2 rounded-xl border bg-panel p-3 shadow-2xl" style={{ borderColor: BIAS_COLOR[t.bias] }}>
              <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: BIAS_COLOR[t.bias] }} />
              <div className="min-w-0 flex-1"><div className="text-sm font-bold">{t.title}</div><div className="note">{t.text}</div></div>
              <button type="button" className="grid h-7 w-7 shrink-0 place-items-center rounded text-mut hover:text-fg" aria-label="Dismiss" onClick={() => onDismiss(t.key)}>✕</button>
            </div>))}
        </div>)}
    </>
  );
}
