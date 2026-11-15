// Candle-sequence patterns (mirrors backend/app/patterns.py): helpers for the builder UI - blocks, mirroring, plain-English text, recipes.
export type Color = 'any' | 'green' | 'red';
export type Shape = 'any' | 'hammer' | 'star' | 'doji' | 'strong' | 'weak' | 'long';
export type Prop = 'open' | 'high' | 'low' | 'close' | 'body_mid' | 'body' | 'range';
export type Cmp = '>' | '<' | '>=' | '<=';
export type Scope = 'each' | 'any' | 'last';
export interface PatRel { prop: Prop; op: Cmp; ref: 'prev' | number; refprop: Prop; scope: Scope }
export interface PatStep { label: string; color: Color; shape: Shape; min: number; max: number; rels: PatRel[] }
export interface PatternForm { steps: PatStep[] }

export const COLORS: { v: Color; label: string }[] = [{ v: 'any', label: 'any colour' }, { v: 'green', label: 'green (up)' }, { v: 'red', label: 'red (down)' }];
export const SHAPES: { v: Shape; label: string; about: string }[] = [
  { v: 'any', label: 'any shape', about: 'No shape requirement.' },
  { v: 'hammer', label: 'hammer (long lower wick)', about: 'Small body near the top, lower wick at least twice the body, almost no upper wick.' },
  { v: 'star', label: 'shooting star (long upper wick)', about: 'Small body near the bottom, upper wick at least twice the body, almost no lower wick.' },
  { v: 'doji', label: 'doji (almost no body)', about: 'Open and close nearly equal.' },
  { v: 'strong', label: 'strong body (60%+ of range)', about: 'Body fills most of the candle: little wick.' },
  { v: 'weak', label: 'weak body (30% or less)', about: 'Small body with wicks: indecision.' },
  { v: 'long', label: 'long candle (bigger than usual)', about: 'Body at least 1.3x the average of the last 14 bodies.' },
];
export const PROPS: { v: Prop; label: string; price: boolean }[] = [
  { v: 'high', label: 'high', price: true }, { v: 'low', label: 'low', price: true }, { v: 'close', label: 'close', price: true }, { v: 'open', label: 'open', price: true },
  { v: 'body_mid', label: 'body middle', price: true }, { v: 'body', label: 'body size', price: false }, { v: 'range', label: 'full size (high-low)', price: false },
];
export const CMPS: { v: Cmp; label: string }[] = [{ v: '>', label: 'is higher than' }, { v: '<', label: 'is lower than' }, { v: '>=', label: 'is at or above' }, { v: '<=', label: 'is at or below' }];
export const SCOPES: { v: Scope; label: string }[] = [{ v: 'each', label: 'on every candle of the step' }, { v: 'any', label: 'on at least one candle' }, { v: 'last', label: 'on the last candle only' }];
export const STEP_COLORS = ['#38bdf8', '#f59e0b', '#e879f9', '#34d399', '#fb7185', '#a3e635', '#f97316', '#22d3ee'];

export const rel = (prop: Prop, op: Cmp, ref: 'prev' | number, refprop: Prop, scope: Scope = 'each'): PatRel => ({ prop, op, ref, refprop, scope });
export const step = (label: string, color: Color, shape: Shape, min = 1, max = min, rels: PatRel[] = []): PatStep => ({ label, color, shape, min, max, rels });
export const blankPattern = (): PatternForm => ({ steps: [step('Step 1', 'any', 'any')] });

/** Draggable single-candle blocks. */
export const CANDLE_BLOCKS: { id: string; label: string; step: () => PatStep }[] = [
  { id: 'green', label: 'Green candle', step: () => step('Green candle', 'green', 'any') },
  { id: 'red', label: 'Red candle', step: () => step('Red candle', 'red', 'any') },
  { id: 'hammer', label: 'Hammer', step: () => step('Hammer', 'any', 'hammer') },
  { id: 'star', label: 'Shooting star', step: () => step('Shooting star', 'any', 'star') },
  { id: 'doji', label: 'Doji', step: () => step('Doji', 'any', 'doji') },
  { id: 'strong', label: 'Strong candle', step: () => step('Strong candle', 'any', 'strong') },
  { id: 'weak', label: 'Weak candle', step: () => step('Weak candle', 'any', 'weak') },
];

// ------------------------------------------------------------------ mirror (BUY <-> SELL)
const flipProp = (p: Prop): Prop => (p === 'high' ? 'low' : p === 'low' ? 'high' : p);
const isPrice = (p: Prop) => PROPS.find((x) => x.v === p)?.price ?? false;
const flipCmp = (c: Cmp): Cmp => (c === '>' ? '<' : c === '<' ? '>' : c === '>=' ? '<=' : '>=');
const FLIP_COLOR: Record<Color, Color> = { any: 'any', green: 'red', red: 'green' };
const FLIP_SHAPE: Record<Shape, Shape> = { any: 'any', hammer: 'star', star: 'hammer', doji: 'doji', strong: 'strong', weak: 'weak', long: 'long' };
const FLIP: [RegExp, string][] = [[/\bgreen\b/gi, 'red'], [/\bred\b/gi, 'green'], [/\bhammer\b/gi, 'shooting star'], [/\bshooting star\b/gi, 'hammer'], [/\buptrend\b/gi, 'downtrend'], [/\bdowntrend\b/gi, 'uptrend'], [/\bbelow\b/gi, 'above'], [/\babove\b/gi, 'below']];

function flipLabel(label: string): string {
  const marks: string[] = [];
  let t = label;
  for (const [re, to] of FLIP) t = t.replace(re, (m) => { marks.push(m[0] === m[0].toUpperCase() ? to[0].toUpperCase() + to.slice(1) : to); return '{' + (marks.length - 1) + '}'; });
  return t.replace(/\{(\d+)\}/g, (_, k: string) => marks[Number(k)]);
}

/** The opposite-direction twin: green<->red, hammer<->shooting star, higher<->lower. */
export function mirror(p: PatternForm): PatternForm {
  return {
    steps: p.steps.map((s) => ({
      ...s, label: flipLabel(s.label), color: FLIP_COLOR[s.color], shape: FLIP_SHAPE[s.shape],
      rels: s.rels.map((r) => (isPrice(r.prop) && isPrice(r.refprop) ? { ...r, prop: flipProp(r.prop), refprop: flipProp(r.refprop), op: flipCmp(r.op) } : { ...r })),
    })),
  };
}

// ------------------------------------------------------------------ plain English
const propText = (p: Prop) => PROPS.find((x) => x.v === p)?.label ?? p;
const cmpText = (c: Cmp) => CMPS.find((x) => x.v === c)?.label ?? c;

export function stepTitle(s: PatStep): string {
  const n = s.min === s.max ? (s.min === 1 ? 'one' : String(s.min)) : `${s.min} to ${s.max}`;
  const shape = s.shape === 'any' ? '' : { hammer: 'hammer ', star: 'shooting-star ', doji: 'doji ', strong: 'strong ', weak: 'weak ', long: 'long ' }[s.shape];
  const color = s.color === 'any' ? '' : `${s.color} `;
  const noun = s.shape === 'hammer' || s.shape === 'star' || s.shape === 'doji' ? (s.max > 1 ? 'candles' : 'candle') : s.max > 1 ? 'candles' : 'candle';
  return `${n} ${color}${shape}${noun}`.replace(/\s+/g, ' ').trim();
}

export function relText(r: PatRel, steps: PatStep[], multi: boolean): string {
  const who = r.ref === 'prev' ? 'the previous candle' : `the "${steps[r.ref]?.label ?? '?'}" candle`;
  const scope = multi ? (r.scope === 'each' ? (r.ref === 'prev' ? ' (each candle vs the one before it)' : ' (every candle)') : r.scope === 'any' ? ' (at least one candle)' : ' (last candle)') : '';
  return `its ${propText(r.prop)} ${cmpText(r.op)} ${who}'s ${propText(r.refprop)}${scope}`;
}

export function describePattern(p: PatternForm): string[] {
  return p.steps.map((s, i) => {
    const rels = s.rels.map((r) => relText(r, p.steps, s.max > 1));
    return `${i + 1}. ${stepTitle(s)}${rels.length ? ': ' + rels.join('; ') : ''}`;
  });
}

// ------------------------------------------------------------------ recipes: complete candle strategies, both directions
export interface PatternRecipe { id: string; title: string; about: string; call: PatternForm; put?: PatternForm }
const build = (title: string, about: string, id: string, call: PatternForm, put?: PatternForm): PatternRecipe => ({ id, title, about, call, put: put ?? mirror(call) });

export const PATTERN_RECIPES: PatternRecipe[] = [
  build('Hammer sweep after a run', 'Several same-colour candles making higher highs, a hammer that dips below the previous low, then red candle(s) that trade below the hammer\'s low: BUY. The mirror image after a downtrend gives SELL.', 'hammer-sweep', {
    steps: [
      step('Uptrend', 'green', 'any', 3, 6, [rel('high', '>', 'prev', 'high')]),
      step('Hammer', 'green', 'hammer', 1, 1, [rel('low', '<', 'prev', 'low')]),
      step('Sweep below the hammer', 'red', 'any', 1, 3, [rel('low', '<', 1, 'low', 'any')]),
    ],
  }),
  build('Three soldiers / three crows', 'Three strong candles in a row, each closing higher than the last: BUY. Three strong falling candles: SELL.', 'three-soldiers', {
    steps: [step('Three soldiers', 'green', 'strong', 3, 3, [rel('close', '>', 'prev', 'close'), rel('high', '>', 'prev', 'high')])],
  }),
  build('Morning / evening star', 'A big red candle, a small indecision candle, then a strong green candle closing above the middle of the first: BUY. Mirror: SELL.', 'morning-star', {
    steps: [step('Big red candle', 'red', 'long'), step('Indecision', 'any', 'weak'), step('Strong green recovery', 'green', 'strong', 1, 1, [rel('close', '>', 0, 'body_mid')])],
  }),
  build('Engulfing after a pullback', 'Two or more red candles, then a green candle that opens below and closes above the last red candle\'s body: BUY. Mirror: SELL.', 'engulfing', {
    steps: [step('Pullback', 'red', 'any', 2, 4), step('Bullish engulfing', 'green', 'any', 1, 1, [rel('close', '>=', 'prev', 'open'), rel('open', '<=', 'prev', 'close'), rel('body', '>', 'prev', 'body')])],
  }),
  build('Pin bar rejection', 'Two or more falling candles, then a hammer whose low sets a new low (price rejected): BUY. Mirror with a shooting star: SELL.', 'pin-bar', {
    steps: [step('Downtrend', 'red', 'any', 2, 5, [rel('low', '<', 'prev', 'low')]), step('Hammer rejection', 'any', 'hammer', 1, 1, [rel('low', '<', 'prev', 'low')])],
  }),
  build('Inside bar breakout', 'A strong candle, a candle contained inside it, then a green candle closing above the first candle\'s high: BUY. Mirror: SELL.', 'inside-break', {
    steps: [
      step('Mother candle', 'any', 'strong'),
      step('Inside bar', 'any', 'any', 1, 1, [rel('high', '<', 'prev', 'high'), rel('low', '>', 'prev', 'low')]),
      step('Breakout', 'green', 'any', 1, 1, [rel('close', '>', 0, 'high')]),
    ],
  }),
  build('Exhaustion doji', 'After 3 or more red candles, a doji appears: sellers are tired, BUY. After green candles: SELL.', 'exhaustion-doji', {
    steps: [step('Run of candles', 'red', 'any', 3, 6), step('Doji', 'any', 'doji')],
  }),
];

// ------------------------------------------------------------------ client-side check (the server validates the same rules)
export function patternProblems(p: PatternForm | null): string[] {
  if (!p || p.steps.length === 0) return [];
  const out: string[] = [];
  p.steps.forEach((s, i) => {
    if (!(Number.isInteger(s.min) && Number.isInteger(s.max) && s.min >= 1 && s.max >= s.min && s.max <= 8)) out.push(`Step ${i + 1}: repeat must be 1-8 with min ≤ max`);
    s.rels.forEach((r) => { if (r.ref !== 'prev' && !(r.ref >= 0 && r.ref < i)) out.push(`Step ${i + 1}: a relation points at a step that does not come before it`); });
  });
  const combos = p.steps.reduce((a, s) => a * Math.max(1, s.max - s.min + 1), 1);
  if (combos > 600) out.push('Too many repeat combinations; narrow the min-max ranges');
  return out;
}
