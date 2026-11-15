import { ReactNode, useEffect, useRef, useState } from 'react';
import {
  blankClause, Clause, clauseText, cmp, describeClause, describeGroup, Group, INDICATOR_INFO, num, Op, OPS, Operand, parse, RECIPES, ref, serialize,
} from '../lib/conditions';
import { CANDLE_BLOCKS, PATTERN_RECIPES } from '../lib/patterns';
import { decode, encode, Hl, Payload, useBuilder } from './builderContext';
import { CandleIcon } from './CandleIcon';

const TONE_CLASS = { call: 'border-up/50 bg-up/5', put: 'border-down/50 bg-down/5' } as const;
const BOOL_FIELDS = ['green', 'red', 'nolow', 'nohigh'];
const PATTERNS = ['green', 'red', 'doji', 'hammer', 'star', 'bull_engulf', 'bear_engulf', 'inside_bar'];
const isBool = (name: string) => PATTERNS.includes(name) || BOOL_FIELDS.includes(name.split('.')[1] ?? '');

// ------------------------------------------------------------------ drag & drop plumbing
export function Chip({ payload, children, color, title }: { payload: Payload; children: ReactNode; color?: string; title?: string }) {
  const { setDrag } = useBuilder();
  return (
    <span draggable title={title ?? 'Drag me into a condition'} style={color ? { borderColor: color, color } : undefined}
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', encode(payload)); e.dataTransfer.effectAllowed = 'copy'; setDrag(payload); }}
      onDragEnd={() => setDrag(null)}
      className="inline-block cursor-grab select-none rounded-md border border-line bg-panel2 px-2 py-1 font-mono text-xs text-fg transition hover:border-acc active:cursor-grabbing">
      {children}
    </span>
  );
}

export function useDrop(accept: (p: Payload) => boolean, onDrop: (p: Payload) => void) {
  const { drag } = useBuilder();
  const [over, setOver] = useState(false);
  const can = !!drag && accept(drag);
  return {
    can, over,
    props: {
      onDragOver: (e: React.DragEvent) => { if (can) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; if (!over) setOver(true); } },
      onDragLeave: () => setOver(false),
      onDrop: (e: React.DragEvent) => {
        e.preventDefault(); e.stopPropagation(); setOver(false);
        const p = decode(e.dataTransfer.getData('text/plain'));
        if (p && accept(p)) onDrop(p);
      },
    },
  };
}
export const dropRing = (d: { can: boolean; over: boolean }) => (d.over ? 'border-acc bg-acc/20 ring-2 ring-acc' : d.can ? 'border-dashed border-acc bg-acc/10' : '');

// ------------------------------------------------------------------ slots
function OperandSlot({ value, onChange, allowNum = true, wide = false }: { value: Operand | null; onChange: (o: Operand | null) => void; allowNum?: boolean; wide?: boolean }) {
  const { names } = useBuilder();
  const drop = useDrop((p) => p.t === 'ref' || (allowNum && p.t === 'num'), (p) => onChange(p.t === 'ref' ? ref(p.name) : p.t === 'num' ? num(p.value) : null));
  const known = new Set(names.flatMap((g) => g.items.map((i) => i.name)));
  const isNum = value?.kind === 'num';
  return (
    <div {...drop.props} className={`flex items-center gap-1 rounded-lg border border-line bg-bg px-1.5 py-1 ${wide ? 'min-w-[190px]' : 'min-w-[150px]'} ${dropRing(drop)}`}>
      {isNum ? (
        <input type="number" step="any" className="w-24 bg-transparent font-mono text-sm outline-none" value={value.value} onChange={(e) => onChange(num(e.target.value))} aria-label="number" />
      ) : (
        <select className="max-w-[210px] bg-transparent font-mono text-sm outline-none" value={!value ? '' : value.kind === 'ref' ? value.name : ''}
          onChange={(e) => { const v = e.target.value; onChange(v === '#num' ? num(0) : ref(v, value?.kind === 'ref' ? value.ago : 0)); }}>
          <option value="" disabled>choose or drop…</option>
          {names.map((g) => g.items.length > 0 && <optgroup key={g.label} label={g.label}>{g.items.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}</optgroup>)}
          {value?.kind === 'ref' && !known.has(value.name) && <option value={value.name}>{value.name} (unknown)</option>}
          {allowNum && <option value="#num">a number…</option>}
        </select>
      )}
      {isNum && <button className="text-xs text-mut hover:text-fg" title="use a name instead of a number" onClick={() => onChange(null)}>↺</button>}
      {value?.kind === 'ref' && (
        <label className="flex items-center text-[10px] text-mut" title="0 = this candle, 1 = one candle ago, 2 = two candles ago …">
          <span>[</span><input type="number" min={0} max={50} className="w-8 bg-transparent text-center font-mono text-xs text-fg outline-none" value={value.ago} onChange={(e) => onChange(ref(value.name, Math.max(0, Math.min(50, Number(e.target.value) || 0))))} /><span>]</span>
        </label>
      )}
    </div>
  );
}

function OpSlot({ op, onChange }: { op: Op; onChange: (o: Op) => void }) {
  const drop = useDrop((p) => p.t === 'op', (p) => p.t === 'op' && onChange(p.op));
  return (
    <div {...drop.props} className={`rounded-lg border border-line bg-bg px-1.5 py-1 ${dropRing(drop)}`}>
      <select className="bg-transparent text-sm font-semibold text-[#c4b5fd] outline-none" value={op} onChange={(e) => onChange(e.target.value as Op)}>
        {OPS.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
      </select>
    </div>
  );
}

// ------------------------------------------------------------------ one sentence
function ClauseRow({ c, onChange, onRemove, prefix }: { c: Clause; onChange: (c: Clause) => void; onRemove: () => void; prefix: string }) {
  const { indicators, marks, n, hover, pin, pinnedKey, hl } = useBuilder();
  const key = `${prefix}|${c.id}`, text = clauseText(c);
  const label = describeClause(c, indicators);
  const mine: Hl = { key, label, tone: 'clause' };
  const m = marks[key];
  const hits = m && !m.error ? m.hits.filter(Boolean).length : null;
  const rawDrop = useDrop((p) => p.t === 'ref', (p) => p.t === 'ref' && onChange({ ...c, raw: `${c.raw ?? ''}${c.raw ? ' ' : ''}${p.name}` }));
  const setOp = (op: Op) => {
    const next = { ...c, op };
    if (op === 'is' || op === 'not') next.right = null;
    else if (op === 'rising' || op === 'falling') next.right = c.right?.kind === 'num' && /^\d+$/.test(c.right.value) ? c.right : num(3);
    onChange(next);
  };
  return (
    <div className={`rounded-lg border bg-panel2 p-2 transition ${hl?.key === key ? 'border-acc' : 'border-line'}`} onMouseEnter={() => hover(mine)} onMouseLeave={() => hover(null)} onFocus={() => hover(mine)} onBlur={() => hover(null)}>
      <div className="flex flex-wrap items-center gap-1.5">
        {c.raw !== undefined ? (
          <div {...rawDrop.props} className={`flex min-w-[260px] flex-1 items-center gap-2 rounded-lg ${dropRing(rawDrop)}`}>
            <span className="chip border-warn/40 text-warn" title="Too specific for the sentence form; edit the expression directly. Drop names here to insert them.">custom</span>
            <input className="input font-mono text-xs" spellCheck={false} value={c.raw} onChange={(e) => onChange({ ...c, raw: e.target.value })} />
          </div>
        ) : (
          <>
            <OperandSlot value={c.left} onChange={(o) => onChange({ ...c, left: o })} allowNum={false} wide />
            <OpSlot op={c.op} onChange={setOp} />
            {c.op === 'rising' || c.op === 'falling' ? (
              <span className="flex items-center gap-1 text-xs text-mut"><input type="number" min={1} max={50} className="input !w-16 !py-1 font-mono" value={c.right?.kind === 'num' ? c.right.value : '3'} onChange={(e) => onChange({ ...c, right: num(e.target.value) })} />candles ago</span>
            ) : c.op !== 'is' && c.op !== 'not' ? <OperandSlot value={c.right} onChange={(o) => onChange({ ...c, right: o })} /> : null}
          </>
        )}
        <span className="ml-auto flex items-center gap-1">
          {m?.error ? <span className="chip border-down/40 text-down" title={m.error}>error</span>
            : hits !== null ? <span className="chip border-line text-mut" title="how many of the charted candles this is true on">{hits}/{n}</span> : null}
          <button className={`btn btn-sm ${pinnedKey === key ? 'border-acc text-[#c4b5fd]' : ''}`} title="Show where this is true on the chart (click to keep it shown)" disabled={!text} onClick={() => pin(mine)}>👁</button>
          <button className="btn btn-sm btn-danger" title="Remove this condition" onClick={onRemove}>✕</button>
        </span>
      </div>
      {(c.raw === undefined || !text) && <div className={`mt-1 text-[11px] ${text ? 'text-mut' : 'text-warn'}`}>{text ? label : 'Incomplete: fill every blank (drag chips from the toolbox or use the dropdowns).'}</div>}
      {m?.error && <div className="mt-0.5 text-[11px] text-down">{m.error}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ a whole CALL / PUT condition
export function ConditionEditor({ value, onChange, side, prefix, title, onExprs, onRecipe }: {
  value: string; onChange: (t: string) => void; side: 'call' | 'put'; prefix: string; title: string;
  onExprs: (prefix: string, map: Record<string, string> | null) => void; onRecipe: (id: string, side: 'call' | 'put') => void;
}) {
  const { indicators, marks, n, hover, pin, pinnedKey, hl } = useBuilder();
  const [g, setG] = useState<Group>(() => parse(value));
  const [textMode, setTextMode] = useState(false);
  const gRef = useRef(g);
  gRef.current = g;
  useEffect(() => { if (value !== serialize(gRef.current)) setG(parse(value)); }, [value]);

  const update = (next: Group) => { setG(next); const t = serialize(next); if (t !== value) onChange(t); };
  const setClause = (i: number, c: Clause) => update({ ...g, clauses: g.clauses.map((x, j) => (j === i ? c : x)) });
  const addClauseFor = (p: Payload) => {
    if (p.t === 'ref') update({ ...g, clauses: [...g.clauses, isBool(p.name) ? cmp(ref(p.name), 'is') : { ...blankClause(), left: ref(p.name) }] });
    else if (p.t === 'recipe') onRecipe(p.id, side);
  };
  const drop = useDrop((p) => p.t === 'ref' || p.t === 'recipe', addClauseFor);

  // report every clause (and the whole condition) to the chart so each can be highlighted
  const expKey = JSON.stringify([serialize(g), g.clauses.map((c) => [c.id, clauseText(c)])]);
  useEffect(() => {
    const map: Record<string, string> = {};
    const whole = serialize(g);
    if (whole) map[prefix] = whole;
    for (const c of g.clauses) { const t = clauseText(c); if (t) map[`${prefix}|${c.id}`] = t; }
    onExprs(prefix, map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expKey, prefix]);
  useEffect(() => () => onExprs(prefix, null), [prefix]); // eslint-disable-line react-hooks/exhaustive-deps

  const whole = serialize(g);
  const mine: Hl = { key: prefix, label: `${title} (all of it)`, tone: side };
  const m = marks[prefix];
  const hits = m && !m.error ? m.hits.filter(Boolean).length : null;
  const sideName = side === 'call' ? 'CALL' : 'PUT';

  return (
    <div className={`rounded-xl border p-3 ${TONE_CLASS[side]} ${hl?.key === prefix ? 'ring-1 ring-acc' : ''}`} onMouseEnter={() => hover(mine)} onMouseLeave={() => hover(null)}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`text-sm font-extrabold ${side === 'call' ? 'text-up' : 'text-down'}`}>{side === 'call' ? '▲ BUY / CALL when' : '▼ SELL / PUT when'}</span>
        {g.clauses.length > 1 && (
          <span className="inline-flex overflow-hidden rounded-lg border border-line text-xs">
            {(['and', 'or'] as const).map((md) => <button key={md} className={`px-2 py-1 ${g.mode === md ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => update({ ...g, mode: md })}>{md === 'and' ? 'ALL of these' : 'ANY of these'}</button>)}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {m?.error ? <span className="chip border-down/40 text-down" title={m.error}>error</span>
            : hits !== null && whole ? <span className="chip border-line text-mut" title={`${sideName} fires on this many of the ${n} charted candles`}>fires on {hits}/{n}</span> : null}
          <button className={`btn btn-sm ${pinnedKey === prefix ? 'border-acc text-[#c4b5fd]' : ''}`} disabled={!whole} title="Show the whole condition on the chart (click to keep it shown)" onClick={() => pin(mine)}>👁</button>
          <button className="btn btn-sm" onClick={() => setTextMode(!textMode)}>{textMode ? 'Back to blocks' : 'Edit as text'}</button>
        </span>
      </div>

      {textMode ? (
        <div>
          <textarea className="input font-mono text-xs" rows={2} spellCheck={false} value={value} placeholder="e.g. rsi14 < 30 and close > ema200" onChange={(e) => onChange(e.target.value)} />
          <p className="note mt-1">Advanced: type the expression directly. Names, functions and examples are in the Toolbox help.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {g.clauses.map((c, i) => (
            <div key={c.id}>
              {i > 0 && <div className="py-0.5 text-center text-[10px] font-bold tracking-widest text-mut">{g.mode === 'and' ? 'AND' : 'OR'}</div>}
              <ClauseRow c={c} prefix={prefix} onChange={(x) => setClause(i, x)} onRemove={() => update({ ...g, clauses: g.clauses.filter((_, j) => j !== i) })} />
            </div>
          ))}
          <div {...drop.props} className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-mut transition ${dropRing(drop)}`}>
            <span>{g.clauses.length === 0 ? `No ${sideName} condition yet: drag a name or a recipe here, or add a blank condition.` : 'Drop another name or recipe here to add one more condition.'}</span>
            <button className="btn btn-sm" onClick={() => update({ ...g, clauses: [...g.clauses, blankClause()] })}>+ Add condition</button>
          </div>
        </div>
      )}
      {whole && !textMode && <div className="mt-2 text-[11px] text-mut"><b className="text-fg">In words:</b> {describeGroup(g, indicators)}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ toolbox (the draggable pieces)
export function Toolbox({ onRecipe, onAddIndicator, onPatternRecipe }: { onRecipe: (id: string) => void; onAddIndicator: (type: string) => void; onPatternRecipe: (id: string) => void }) {
  const { names, indicators } = useBuilder();
  const Sec = ({ title, open = true, children }: { title: string; open?: boolean; children: ReactNode }) => (
    <details open={open} className="border-t border-line pt-2 first:border-0 first:pt-0"><summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-mut">{title}</summary><div className="mt-2">{children}</div></details>
  );
  return (
    <div className="space-y-3 text-xs">
      <p className="note">Drag any piece into a blank of a condition on the left, or drop it on a CALL / PUT box to start a new condition. Prefer clicking? Every blank is also a dropdown.</p>
      <Sec title="Candle patterns (click to use, or drag onto a factor's CALL / PUT)">
        <div className="space-y-1.5">
          {PATTERN_RECIPES.map((r) => (
            <div key={r.id} className="flex items-start justify-between gap-2 rounded-lg border border-line bg-panel2 p-2">
              <div className="min-w-0"><Chip payload={{ t: 'pattern', id: r.id }} title={r.about}>{r.title}</Chip><div className="mt-1 text-[11px] leading-snug text-mut">{r.about}</div></div>
              <button className="btn btn-sm shrink-0" title="Turn the factor you are editing into this candle pattern (BUY and SELL)" onClick={() => onPatternRecipe(r.id)}>Use</button>
            </div>
          ))}
        </div>
      </Sec>
      <Sec title="Candle blocks (drag into a candle pattern)" open={false}>
        <div className="flex flex-wrap gap-1.5">{CANDLE_BLOCKS.map((b) => {
          const st = b.step();
          return <Chip key={b.id} payload={{ t: 'candle', id: b.id }} title={`Drag into a candle pattern to add a "${b.label}" step`}><span className="flex items-center gap-1.5"><span className="inline-block origin-left scale-50" style={{ width: 13, height: 22 }}><CandleIcon color={st.color} shape={st.shape} /></span>{b.label}</span></Chip>;
        })}</div>
      </Sec>
      <Sec title="Quick recipes (click to add, or drag onto CALL / PUT)">
        <div className="space-y-1.5">
          {RECIPES.map((r) => (
            <div key={r.id} className="flex items-start justify-between gap-2 rounded-lg border border-line bg-panel2 p-2">
              <div className="min-w-0"><Chip payload={{ t: 'recipe', id: r.id }} title={r.about}>{r.title}</Chip><div className="mt-1 text-[11px] leading-snug text-mut">{r.about}</div></div>
              <button className="btn btn-sm shrink-0" title="Add to the factor you are editing (adds missing indicators too)" onClick={() => onRecipe(r.id)}>Use</button>
            </div>
          ))}
        </div>
      </Sec>
      <Sec title="Your indicators">
        {indicators.length === 0 ? <div className="note">None yet. Add one in the Indicators card (or from Quick recipes).</div> : null}
        {names.filter((gr) => gr.label === 'Your indicators').map((gr) => <div key={gr.label} className="flex flex-wrap gap-1.5">{gr.items.map((i) => <Chip key={i.name} payload={{ t: 'ref', name: i.name }} color={i.color} title={i.text}>{i.name}</Chip>)}</div>)}
      </Sec>
      {names.filter((gr) => gr.label !== 'Your indicators').map((gr) => (
        <Sec key={gr.label} title={gr.label} open={gr.label === 'Price & candle'}>
          <div className="flex flex-wrap gap-1.5">{gr.items.map((i) => <Chip key={i.name} payload={{ t: 'ref', name: i.name }} title={i.text}>{i.name}</Chip>)}</div>
        </Sec>
      ))}
      <Sec title="Compare with">
        <div className="flex flex-wrap gap-1.5">{OPS.map((o) => <Chip key={o.op} payload={{ t: 'op', op: o.op }} title={o.label}>{o.sentence}</Chip>)}</div>
      </Sec>
      <Sec title="Numbers">
        <div className="flex flex-wrap gap-1.5">{['0', '20', '30', '50', '70', '80', '100', '-100'].map((v) => <Chip key={v} payload={{ t: 'num', value: v }}>{v}</Chip>)}</div>
      </Sec>
      <Sec title="Add an indicator (drag into the Indicators card, or click)" open={false}>
        <div className="flex flex-wrap gap-1.5">{Object.entries(INDICATOR_INFO).map(([t, i]) => <span key={t} onDoubleClick={() => onAddIndicator(t)}><Chip payload={{ t: 'indicator', type: t }} title={`${i.about} (double-click to add)`}>{i.title}</Chip></span>)}</div>
      </Sec>
      <Sec title="Syntax help (for 'Edit as text')" open={false}>
        <div className="space-y-1 font-mono text-[11px] text-mut">
          <div>rsi14 &lt; 30 and close &gt; ema200</div><div>cross_above(macd.hist, 0)</div><div>close[1] &lt; bb.lower[1] and close &gt; bb.lower</div>
          <div>(bull_engulf or hammer) and low &lt;= lowest(low, 20)[1]</div>
          <div className="font-sans">x[1] = one candle ago. Conditions are checked on the last closed candle and predict the NEXT candle. Functions: cross_above, cross_below, highest, lowest, abs, min, max, sma, ema, change, sign, count, rising, falling.</div>
        </div>
      </Sec>
    </div>
  );
}

