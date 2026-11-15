import { useEffect } from 'react';
import {
  CANDLE_BLOCKS, CMPS, COLORS, Cmp, describePattern, PatRel, PatStep, patternProblems, PatternForm, PATTERN_RECIPES, Prop, PROPS, rel, SCOPES, Scope, Shape, SHAPES, STEP_COLORS, step, stepTitle,
} from '../lib/patterns';
import { Hl, useBuilder } from './builderContext';
import { CandleIcon } from './CandleIcon';
import { dropRing, useDrop } from './ConditionEditor';

const sel = 'rounded-md border border-line bg-bg px-1.5 py-1 text-xs outline-none focus:border-acc';

function RelRow({ r, steps, index, multi, onChange, onRemove }: { r: PatRel; steps: PatStep[]; index: number; multi: boolean; onChange: (r: PatRel) => void; onRemove: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1 rounded-md bg-panel2 p-1.5 text-xs text-mut">
      <span>its</span>
      <select className={sel} value={r.prop} onChange={(e) => onChange({ ...r, prop: e.target.value as Prop })}>{PROPS.map((p) => <option key={p.v} value={p.v}>{p.label}</option>)}</select>
      <select className={sel} value={r.op} onChange={(e) => onChange({ ...r, op: e.target.value as Cmp })}>{CMPS.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}</select>
      <select className={sel} value={String(r.ref)} onChange={(e) => onChange({ ...r, ref: e.target.value === 'prev' ? 'prev' : Number(e.target.value) })}>
        <option value="prev">the previous candle's</option>
        {steps.slice(0, index).map((s, i) => <option key={i} value={i}>{`step ${i + 1} (${s.label})'s`}</option>)}
      </select>
      <select className={sel} value={r.refprop} onChange={(e) => onChange({ ...r, refprop: e.target.value as Prop })}>{PROPS.map((p) => <option key={p.v} value={p.v}>{p.label}</option>)}</select>
      {multi && <select className={sel} value={r.scope} title="This step can have several candles: where must the rule hold?" onChange={(e) => onChange({ ...r, scope: e.target.value as Scope })}>{SCOPES.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}</select>}
      <button className="ml-auto text-down hover:underline" title="Remove this rule" onClick={onRemove}>✕</button>
    </div>
  );
}

function StepCard({ i, s, steps, last, onChange, onRemove, onMove }: { i: number; s: PatStep; steps: PatStep[]; last: boolean; onChange: (s: PatStep) => void; onRemove: () => void; onMove: (d: -1 | 1) => void }) {
  const col = STEP_COLORS[i % STEP_COLORS.length];
  const num = (v: string, d: number) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.max(1, Math.min(8, n)) : d; };
  return (
    <div className="min-w-[270px] flex-1 rounded-xl border bg-panel p-2.5" style={{ borderColor: col + '88' }}>
      <div className="flex items-center gap-2">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold text-black" style={{ background: col }}>{i + 1}</span>
        <input className="input !py-1 text-sm font-semibold" value={s.label} onChange={(e) => onChange({ ...s, label: e.target.value })} aria-label="step name" />
        <button className="btn btn-sm" disabled={i === 0} title="Move earlier" onClick={() => onMove(-1)}>←</button>
        <button className="btn btn-sm" disabled={last} title="Move later" onClick={() => onMove(1)}>→</button>
        <button className="btn btn-sm btn-danger" title="Remove this step" onClick={onRemove}>✕</button>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <CandleIcon color={s.color} shape={s.shape} count={s.max} />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs text-mut">
            <span>in a row:</span>
            <input type="number" min={1} max={8} className={`${sel} w-12`} value={s.min} onChange={(e) => { const v = num(e.target.value, s.min); onChange({ ...s, min: v, max: Math.max(v, s.max) }); }} />
            <span>to</span>
            <input type="number" min={1} max={8} className={`${sel} w-12`} value={s.max} onChange={(e) => { const v = num(e.target.value, s.max); onChange({ ...s, max: v, min: Math.min(v, s.min) }); }} />
            <span>candles</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <select className={sel} value={s.color} onChange={(e) => onChange({ ...s, color: e.target.value as PatStep['color'] })}>{COLORS.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}</select>
            <select className={sel} value={s.shape} title={SHAPES.find((x) => x.v === s.shape)?.about} onChange={(e) => onChange({ ...s, shape: e.target.value as Shape })}>{SHAPES.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}</select>
          </div>
        </div>
      </div>
      <div className="mt-2 space-y-1.5">
        {s.rels.map((r, k) => (
          <RelRow key={k} r={r} steps={steps} index={i} multi={s.max > 1}
            onChange={(x) => onChange({ ...s, rels: s.rels.map((y, j) => (j === k ? x : y)) })} onRemove={() => onChange({ ...s, rels: s.rels.filter((_, j) => j !== k) })} />))}
        {s.rels.length < 4 && <button className="btn btn-sm" onClick={() => onChange({ ...s, rels: [...s.rels, rel(i === 0 ? 'high' : 'low', i === 0 ? '>' : '<', 'prev', i === 0 ? 'high' : 'low')] })}>+ Add a rule about price</button>}
      </div>
      <div className="mt-1.5 text-[11px] text-mut">{stepTitle(s)}</div>
    </div>
  );
}

// ------------------------------------------------------------------ one whole CALL / PUT pattern
export function PatternEditor({ value, onChange, side, prefix, title, onPattern, onRecipe }: {
  value: PatternForm | null; onChange: (p: PatternForm | null) => void; side: 'call' | 'put'; prefix: string; title: string;
  onPattern: (prefix: string, p: PatternForm | null) => void; onRecipe: (id: string, side: 'call' | 'put') => void;
}) {
  const { marks, n, hover, pin, pinnedKey, hl } = useBuilder();
  const steps = value?.steps ?? [];
  const problems = patternProblems(value);
  useEffect(() => { onPattern(prefix, value && value.steps.length && problems.length === 0 ? value : null); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [JSON.stringify(value), prefix]);
  useEffect(() => () => onPattern(prefix, null), [prefix]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (next: PatStep[]) => onChange(next.length ? { steps: next } : null);
  const drop = useDrop((p) => p.t === 'candle' || p.t === 'pattern', (p) => {
    if (p.t === 'candle') { const b = CANDLE_BLOCKS.find((x) => x.id === p.id); if (b) set([...steps, b.step()]); }
    else if (p.t === 'pattern') onRecipe(p.id, side);
  });
  const mine: Hl = { key: prefix, label: `${title}`, tone: side, steps: steps.map((s) => s.label) };
  const m = marks[prefix];
  const hits = m && !m.error ? m.hits.filter(Boolean).length : null;
  const sideName = side === 'call' ? 'CALL' : 'PUT';
  const ex = PATTERN_RECIPES[0];

  return (
    <div className={`rounded-xl border p-3 ${side === 'call' ? 'border-up/50 bg-up/5' : 'border-down/50 bg-down/5'} ${hl?.key === prefix ? 'ring-1 ring-acc' : ''}`} onMouseEnter={() => hover(mine)} onMouseLeave={() => hover(null)}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`text-sm font-extrabold ${side === 'call' ? 'text-up' : 'text-down'}`}>{side === 'call' ? '▲ BUY / CALL when these candles appear' : '▼ SELL / PUT when these candles appear'}</span>
        <span className="ml-auto flex items-center gap-1">
          {m?.error ? <span className="chip border-down/40 text-down" title={m.error}>error</span>
            : hits !== null && steps.length ? <span className="chip border-line text-mut" title={`This pattern ended on ${hits} of the ${n} charted candles`}>found {hits}× in {n}</span> : null}
          <button className={`btn btn-sm ${pinnedKey === prefix ? 'border-acc text-[#c4b5fd]' : ''}`} disabled={!steps.length} title="Show where this pattern occurs on the chart (click to keep it shown)" onClick={() => pin(mine)}>👁</button>
        </span>
      </div>

      <div {...drop.props} className={`rounded-lg border border-transparent p-1 transition ${dropRing(drop)}`}>
        {steps.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line p-4 text-center text-xs text-mut">
            No {sideName} pattern yet. Drag a candle block or a pattern recipe here, or
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              <button className="btn btn-sm" onClick={() => set([step('Step 1', 'any', 'any')])}>+ Add the first candle step</button>
              <button className="btn btn-sm" onClick={() => onRecipe(ex.id, side)}>Start from the "{ex.title}" example</button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-stretch gap-2">
            {steps.map((s, i) => (
              <div key={i} className="flex min-w-[270px] flex-1 items-stretch gap-2">
                <StepCard i={i} s={s} steps={steps} last={i === steps.length - 1}
                  onChange={(x) => set(steps.map((y, j) => (j === i ? x : y)))}
                  onRemove={() => set(steps.filter((_, j) => j !== i).map((y) => ({ ...y, rels: y.rels.filter((r) => r.ref !== i).map((r) => (r.ref !== 'prev' && r.ref > i ? { ...r, ref: r.ref - 1 } : r)) })))}
                  onMove={(d) => {
                    const j = i + d; if (j < 0 || j >= steps.length) return;
                    const a = [...steps]; [a[i], a[j]] = [a[j], a[i]];
                    // relations that pointed at the swapped steps follow them; any that would now point forward fall back to the previous candle
                    const remap = (r: PatRel, at: number): PatRel => {
                      if (r.ref === 'prev') return r;
                      const to = r.ref === i ? j : r.ref === j ? i : r.ref;
                      return to < at ? { ...r, ref: to } : { ...r, ref: 'prev' };
                    };
                    set(a.map((st, at) => ({ ...st, rels: st.rels.map((r) => remap(r, at)) })));
                  }} />
              </div>
            ))}
            <div className="flex min-w-[150px] items-center justify-center rounded-xl border border-dashed border-line p-3 text-center text-xs text-mut">
              <div>Drop a candle block here<br /><button className="btn btn-sm mt-2" onClick={() => set([...steps, step(`Step ${steps.length + 1}`, 'any', 'any')])}>+ Add step</button></div>
            </div>
          </div>
        )}
      </div>

      {steps.length > 0 && (
        <div className="mt-2 text-[11px] text-mut">
          <b className="text-fg">In words</b> (the last candle of the last step is the candle that just closed; the signal is for the NEXT candle):
          <ol className="mt-1 space-y-0.5">{describePattern({ steps }).map((l) => <li key={l}>{l}</li>)}</ol>
        </div>
      )}
      {problems.map((p) => <div key={p} className="mt-1 text-[11px] text-warn">{p}</div>)}
      {m?.error && <div className="mt-1 text-[11px] text-down">{m.error}</div>}
    </div>
  );
}

