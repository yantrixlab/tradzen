import { ReactNode } from 'react';

/** Form controls shared by the settings panels (Trading guard, Money management): a switch, a number box with a prefix / suffix, a segmented choice and a labelled row. */

export const Toggle = ({ on, onChange, label, hint }: { on: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode }) => (
  <label className="flex cursor-pointer items-start gap-3">
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full border transition ${on ? 'border-acc bg-acc' : 'border-line bg-panel2'}`}>
      <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
    <span className="min-w-0"><span className="block text-sm font-medium text-fg">{label}</span>{hint && <span className="block text-xs text-mut">{hint}</span>}</span>
  </label>
);

export function Num({ value, onChange, prefix, suffix, min = 0, max, step = 1, disabled, w = 'w-28', label }: {
  value: number; onChange: (v: number) => void; prefix?: string; suffix?: string; min?: number; max?: number; step?: number; disabled?: boolean; w?: string; label?: string;
}) {
  return (
    <div className={`flex items-center overflow-hidden rounded-lg border border-line bg-panel2 focus-within:border-acc ${disabled ? 'opacity-50' : ''} ${w}`}>
      {prefix && <span className="pl-2 text-sm text-mut">{prefix}</span>}
      <input type="number" aria-label={label} className="w-full bg-transparent px-2 py-1.5 text-sm max-sm:py-2.5 tabular-nums text-fg outline-none" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step} disabled={disabled}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />
      {suffix && <span className="pr-2 text-sm text-mut">{suffix}</span>}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-line" role="group" aria-label={label}>
      {options.map(([k, l]) => <button key={k} type="button" className={`px-2.5 py-1.5 text-xs font-semibold transition ${value === k ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  );
}

export const Row = ({ children, label, hint }: { children: ReactNode; label: ReactNode; hint?: ReactNode }) => (
  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line py-3 first:border-t-0">
    <div className="min-w-[180px] flex-1"><div className="text-sm font-medium">{label}</div>{hint && <div className="text-xs text-mut">{hint}</div>}</div>
    <div className="flex flex-wrap items-center gap-2">{children}</div>
  </div>
);

