import { ReactNode, useEffect } from 'react';
import { verdict } from '../format';

export const Card = ({ title, right, children, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={`card ${className}`}>
    {(title || right) && <h2 className="card-title"><span>{title}</span><span className="normal-case tracking-normal">{right}</span></h2>}
    {children}
  </section>
);

/**
 * A scrolling list that fills ALL the height its card has (put the card in a grid row beside a taller card and give it className="flex flex-col").
 * The list is absolutely positioned inside, so its own length never stretches the row; it scrolls instead, and never leaves empty card space below it.
 */
export const FillList = ({ children, min = 440 }: { children: ReactNode; min?: number }) => (
  <div className="relative flex-1" style={{ minHeight: min }}><div className="absolute inset-0 overflow-auto">{children}</div></div>
);

export const Stat = ({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'up' | 'down' | 'warn' }) => (
  <div className="rounded-lg border border-line bg-panel2 p-3">
    <div className="text-[11px] font-semibold uppercase tracking-wide text-mut">{label}</div>
    <div className={`mt-0.5 text-2xl font-bold tabular-nums ${tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : tone === 'warn' ? 'text-warn' : ''}`}>{value}</div>
    {sub && <div className="note mt-0.5">{sub}</div>}
  </div>
);

const TONES: Record<string, string> = {
  up: 'border-up/40 bg-up/10 text-up', down: 'border-down/40 bg-down/10 text-down', warn: 'border-warn/40 bg-warn/10 text-warn',
  acc: 'border-acc/50 bg-acc/15 text-[#c4b5fd]', mut: 'border-line bg-panel2 text-mut',
};
export const Badge = ({ tone = 'mut', children, title }: { tone?: string; children: ReactNode; title?: string }) => (
  <span className={`chip ${TONES[tone] ?? TONES.mut}`} title={title}>{children}</span>
);

export const StatusBadge = ({ status }: { status: string }) => {
  const tone = status === 'live' ? 'up' : status === 'paper' ? 'acc' : status === 'paused' ? 'warn' : 'mut';
  return <Badge tone={tone}>{status.toUpperCase()}</Badge>;
};
export const ResultBadge = ({ status }: { status: string }) => {
  const tone = status === 'win' ? 'up' : status === 'loss' ? 'down' : status === 'pending' ? 'warn' : 'mut';
  return <Badge tone={tone}>{status.toUpperCase()}</Badge>;
};
export const DirBadge = ({ dir }: { dir: string }) =>
  dir === 'CALL' ? <span className="font-bold text-up">▲ CALL</span> : dir === 'PUT' ? <span className="font-bold text-down">▼ PUT</span> : <span className="text-mut">SKIP</span>;

export const VerdictBadge = ({ settled, lo, hi, be }: { settled: number; lo: number | null; hi: number | null; be: number | null }) => {
  const v = verdict(settled, lo, hi, be);
  const map = { above: ['up', 'ABOVE BREAK-EVEN'], below: ['down', 'BELOW BREAK-EVEN'], near: ['mut', 'NEAR BREAK-EVEN'], sample: ['warn', 'SMALL SAMPLE'], none: ['mut', 'NO DATA'] } as const;
  return <Badge tone={map[v][0]}>{map[v][1]}</Badge>;
};

/** small green bubble with the number of streaming pairs, pinned to the top-right corner of a (relative) tab button; hidden when 0 */
export const StreamBubble = ({ n }: { n: number }) => n > 0 ? (
  <span title={`${n} streaming now`} className="absolute -right-1.5 -top-2 grid h-5 min-w-5 place-items-center rounded-full border-2 border-bg bg-up px-1 text-[11px] font-bold leading-none text-[#04210f] shadow-[0_0_8px_#22c55e99]">{n}</span>
) : null;

export const Spinner = ({ label = 'Loading…' }: { label?: string }) => <div className="p-6 text-center text-mut">{label}</div>;
export const ErrorBox = ({ error }: { error: unknown }) => (
  <div className="rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">{error instanceof Error ? error.message : String(error)}</div>
);
export const Empty = ({ children }: { children: ReactNode }) => <div className="p-6 text-center text-mut">{children}</div>;

export const Tabs = ({ tabs, value, onChange }: { tabs: { key: string; label: string }[]; value: string; onChange: (k: string) => void }) => (
  <div className="mb-4 flex gap-1 border-b border-line">
    {tabs.map((t) => (
      <button key={t.key} onClick={() => onChange(t.key)}
        className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${value === t.key ? 'border-acc text-fg' : 'border-transparent text-mut hover:text-fg'}`}>
        {t.label}
      </button>
    ))}
  </div>
);

export const Drawer = ({ open, onClose, title, children, width = 560 }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number }) => {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    if (open) window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <aside className="h-full overflow-y-auto border-l border-line bg-bg p-5 shadow-2xl" style={{ width: `min(100vw, ${width}px)` }} onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 className="text-lg font-bold">{title}</h2>
          <button className="btn btn-sm" onClick={onClose}>Close</button>
        </div>
        {children}
      </aside>
    </div>
  );
};

export const Modal = ({ open, onClose, title, children, width = 720 }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="max-h-[90vh] overflow-y-auto rounded-xl border border-line bg-bg p-5 shadow-2xl" style={{ width: `min(100%, ${width}px)` }} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="text-lg font-bold">{title}</h2>
          <button className="btn btn-sm" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  );
};

export const Field = ({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) => (
  <label className="block"><span className="label">{label}</span>{children}{hint && <span className="note mt-1 block">{hint}</span>}</label>
);

export const Progress = ({ value }: { value: number }) => (
  <div className="h-1.5 w-full overflow-hidden rounded bg-line"><div className="h-full rounded bg-acc" style={{ width: `${Math.round(value * 100)}%` }} /></div>
);

export const PageTitle = ({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) => (
  <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
    <div><h1 className="text-2xl font-bold">{title}</h1>{sub && <p className="mt-1 text-sm text-mut">{sub}</p>}</div>
    <div className="flex items-center gap-2">{right}</div>
  </div>
);
