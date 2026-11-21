// Structured form of a condition <-> the expression text the backend evaluates. Lets the builder offer drag-and-drop sentences
// ("close  is above  ema50") while the saved spec stays the plain text language. Anything the sentence form cannot express
// (arithmetic, nested and/or, other functions) is kept as a "custom" clause, so no existing strategy loses information.
import type { IndicatorForm } from './specModel';

export type Operand = { kind: 'num'; value: string } | { kind: 'ref'; name: string; ago: number };
export type Op = '>' | '<' | '>=' | '<=' | 'cross_above' | 'cross_below' | 'rising' | 'falling' | 'is' | 'not';
export interface Clause { id: string; raw?: string; left: Operand | null; op: Op; right: Operand | null }
export interface Group { mode: 'and' | 'or'; clauses: Clause[] }

let seq = 0;
export const newId = () => `c${++seq}`;
export const blankClause = (): Clause => ({ id: newId(), left: null, op: '>', right: null });
export const ref = (name: string, ago = 0): Operand => ({ kind: 'ref', name, ago });
export const num = (v: number | string): Operand => ({ kind: 'num', value: String(v) });
export const cmp = (left: Operand, op: Op, right?: Operand): Clause => ({ id: newId(), left, op, right: right ?? null });

export const OPS: { op: Op; label: string; sentence: string }[] = [
  { op: '>', label: 'is above  >', sentence: 'is above' }, { op: '<', label: 'is below  <', sentence: 'is below' },
  { op: '>=', label: 'is at or above  >=', sentence: 'is at or above' }, { op: '<=', label: 'is at or below  <=', sentence: 'is at or below' },
  { op: 'cross_above', label: 'crosses above', sentence: 'crosses above' }, { op: 'cross_below', label: 'crosses below', sentence: 'crosses below' },
  { op: 'rising', label: 'is rising for N candles', sentence: 'is higher than it was' }, { op: 'falling', label: 'is falling for N candles', sentence: 'is lower than it was' },
  { op: 'is', label: 'is true (pattern / colour)', sentence: 'is true' }, { op: 'not', label: 'is NOT true', sentence: 'is not true' },
];
export const needsRight = (op: Op) => op !== 'is' && op !== 'not';

// ------------------------------------------------------------------ serialise
const operandText = (o: Operand | null): string | null => {
  if (!o) return null;
  if (o.kind === 'num') return o.value.trim() !== '' && Number.isFinite(Number(o.value)) ? String(Number(o.value)) : null;
  return o.name ? o.name + (o.ago > 0 ? `[${o.ago}]` : '') : null;
};

/** The clause as expression text, or null while it is still incomplete. */
export function clauseText(c: Clause): string | null {
  if (c.raw !== undefined) return c.raw.trim() || null;
  const l = operandText(c.left);
  if (l === null) return null;
  if (c.op === 'is') return l;
  if (c.op === 'not') return `not ${l}`;
  const r = operandText(c.right);
  if (r === null) return null;
  if (c.op === 'cross_above' || c.op === 'cross_below') return `${c.op}(${l}, ${r})`;
  if (c.op === 'rising' || c.op === 'falling') return Number.isInteger(Number(r)) && Number(r) >= 1 ? `${c.op}(${l}, ${r})` : null;
  return `${l} ${c.op} ${r}`;
}

export function serialize(g: Group): string {
  const parts = g.clauses.map((c) => ({ c, t: clauseText(c) })).filter((x): x is { c: Clause; t: string } => x.t !== null);
  if (parts.length === 1) return parts[0].t;
  return parts.map(({ c, t }) => (c.raw !== undefined && /\b(and|or)\b/.test(t) ? `(${t})` : t)).join(g.mode === 'and' ? ' and ' : ' or ');
}

// ------------------------------------------------------------------ parse
const NUM = /^-?\d+(\.\d+)?$/;
const REF = /^([a-z_][a-z0-9_]*)(\.[a-z_][a-z0-9_]*)?(?:\[(\d+)\])?$/;

function parseOperand(s: string): Operand | null {
  const t = s.trim();
  if (NUM.test(t)) return num(t);
  const m = REF.exec(t);
  return m ? ref(m[1] + (m[2] ?? ''), m[3] ? Number(m[3]) : 0) : null;
}

/** Split points outside any brackets: a keyword (and / or, whitespace-delimited) or a comma. */
function topLevel(text: string, words: string[]): { at: number; word: string }[] {
  const out: { at: number; word: string }[] = [];
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '(' || ch === '[') depth++;
    else if (ch === ')' || ch === ']') depth--;
    else if (depth === 0) {
      for (const w of words) {
        const hit = w === ',' ? ch === ',' : text.startsWith(w, i) && /[\s)]/.test(text[i - 1] ?? '') && /[\s(]/.test(text[i + w.length] ?? '');
        if (hit) { out.push({ at: i, word: w }); break; }
      }
    }
  }
  return out;
}

function unwrap(s: string): string {
  let t = s.trim();
  while (t.startsWith('(') && t.endsWith(')')) {
    let depth = 0, closesAtEnd = true;
    for (let i = 0; i < t.length; i++) {
      if (t[i] === '(') depth++;
      else if (t[i] === ')') { depth--; if (depth === 0 && i < t.length - 1) { closesAtEnd = false; break; } }
    }
    if (!closesAtEnd) break;
    t = t.slice(1, -1).trim();
  }
  return t;
}

function parseClause(part: string): Clause {
  const t = unwrap(part);
  const raw = (): Clause => ({ id: newId(), raw: t, left: null, op: '>', right: null });
  let m = /^not\s+(.+)$/.exec(t);
  if (m) { const o = parseOperand(m[1]); if (o && o.kind === 'ref') return cmp(o, 'not'); return raw(); }
  const single = parseOperand(t);
  if (single && single.kind === 'ref') return cmp(single, 'is');
  m = /^(cross_above|cross_below|rising|falling)\((.*)\)$/.exec(t);
  if (m) {
    const args = topLevel(m[2], [',']);
    if (args.length === 1) {
      const a = parseOperand(m[2].slice(0, args[0].at)), b = parseOperand(m[2].slice(args[0].at + 1));
      if (a && b) {
        if (m[1].startsWith('cross')) return cmp(a, m[1] as Op, b);
        if (b.kind === 'num' && /^\d+$/.test(b.value)) return cmp(a, m[1] as Op, b);
      }
    }
    return raw();
  }
  m = /^(.+?)\s*(>=|<=|>|<)\s*(.+)$/.exec(t);
  if (m && !/[<>=]/.test(m[1]) && !/[<>=]/.test(m[3])) {
    const a = parseOperand(m[1]), b = parseOperand(m[3]);
    if (a && b) return cmp(a, m[2] as Op, b);
  }
  return raw();
}

export function parse(text: string): Group {
  const t = (text ?? '').trim();
  if (!t) return { mode: 'and', clauses: [] };
  const splits = topLevel(t, ['and', 'or']);
  if (!splits.length) {
    const inner = unwrap(t);
    if (inner !== t) return parse(inner);
    return { mode: 'and', clauses: [parseClause(t)] };
  }
  const kinds = new Set(splits.map((s) => s.word));
  if (kinds.size > 1) return { mode: 'and', clauses: [{ id: newId(), raw: t, left: null, op: '>', right: null }] };
  const parts: string[] = [];
  let from = 0;
  for (const s of splits) { parts.push(t.slice(from, s.at)); from = s.at + s.word.length; }
  parts.push(t.slice(from));
  return { mode: splits[0].word === 'or' ? 'or' : 'and', clauses: parts.map(parseClause) };
}

// ------------------------------------------------------------------ plain-English description
const TYPE_NAME: Record<string, string> = {
  rsi: 'RSI', ema: 'EMA', sma: 'SMA', bollinger: 'Bollinger', macd: 'MACD', stoch: 'Stochastic', adx: 'ADX', atr: 'ATR', cci: 'CCI',
  williams: 'Williams %R', roc: 'ROC', supertrend: 'Supertrend', psar: 'Parabolic SAR', heikin: 'Heikin-Ashi', donchian: 'Donchian', zones: 'Supply/Demand zones', structure: 'Market structure', engulf: 'Engulfing-candle zones', weak: 'Weak high / low',
};
const BASE_TEXT: Record<string, string> = {
  open: 'open price', high: 'high', low: 'low', close: 'close price', ticks: 'tick count', body: 'candle body', range: 'candle range', upper_wick: 'upper wick',
  lower_wick: 'lower wick', green: 'green candle', red: 'red candle', streak: 'colour streak', hlc3: 'typical price (H+L+C)/3', hour: 'hour (UTC)', weekday: 'weekday',
  bull_engulf: 'bullish engulfing candle', bear_engulf: 'bearish engulfing candle', hammer: 'hammer candle', star: 'shooting-star candle', doji: 'doji candle', inside_bar: 'inside bar',
};

export function indicatorTitle(ind: IndicatorForm): string {
  const params = Object.values(ind.params).filter((v) => Number.isFinite(v)).join(',');
  return `${TYPE_NAME[ind.type] ?? ind.type}(${params})`;
}

export function nameText(name: string, inds: IndicatorForm[]): string {
  const [base, field] = name.split('.');
  const ind = inds.find((i) => i.id === base);
  if (ind) return indicatorTitle(ind) + (field ? ` ${field}` : '');
  return BASE_TEXT[name] ?? name;
}

export function operandLabel(o: Operand | null, inds: IndicatorForm[]): string {
  if (!o) return '…';
  if (o.kind === 'num') return o.value || '…';
  return nameText(o.name, inds) + (o.ago > 0 ? ` (${o.ago} candle${o.ago === 1 ? '' : 's'} ago)` : '');
}

export function describeClause(c: Clause, inds: IndicatorForm[]): string {
  if (c.raw !== undefined) return c.raw ? `(${c.raw})` : '…';
  const l = operandLabel(c.left, inds);
  switch (c.op) {
    case 'is': return `${l} is true`;
    case 'not': return `${l} is not true`;
    case 'rising': return `${l} is higher than ${c.right && c.right.kind === 'num' ? c.right.value : '…'} candles ago`;
    case 'falling': return `${l} is lower than ${c.right && c.right.kind === 'num' ? c.right.value : '…'} candles ago`;
    default: return `${l} ${OPS.find((o) => o.op === c.op)?.sentence ?? c.op} ${operandLabel(c.right, inds)}`;
  }
}

export function describeGroup(g: Group, inds: IndicatorForm[]): string {
  const parts = g.clauses.filter((c) => clauseText(c) !== null).map((c) => describeClause(c, inds));
  return parts.join(g.mode === 'and' ? '  AND  ' : '  OR  ');
}

// ------------------------------------------------------------------ indicators the user can add + recipes
export const INDICATOR_INFO: Record<string, { title: string; panel: 'price' | 'osc' | 'none'; about: string }> = {
  sma: { title: 'SMA', panel: 'price', about: 'Simple moving average: the average close over N candles.' },
  ema: { title: 'EMA', panel: 'price', about: 'Exponential moving average: like SMA but reacts faster to recent prices.' },
  bollinger: { title: 'Bollinger Bands', panel: 'price', about: 'A moving average with bands k standard deviations away; price outside a band is stretched.' },
  donchian: { title: 'Donchian channel', panel: 'price', about: 'Highest high / lowest low of the last N candles; breakouts leave the channel.' },
  supertrend: { title: 'Supertrend', panel: 'price', about: 'Trailing trend line; .dir is +1 in an uptrend and -1 in a downtrend.' },
  psar: { title: 'Parabolic SAR', panel: 'price', about: 'Dots that trail price and flip sides when the trend reverses; .dir is +1 / -1.' },
  rsi: { title: 'RSI', panel: 'osc', about: 'Momentum 0-100. Below 30 is often called oversold, above 70 overbought.' },
  stoch: { title: 'Stochastic', panel: 'osc', about: 'Where the close sits within the recent range, 0-100, with k and d lines.' },
  macd: { title: 'MACD', panel: 'osc', about: 'Difference of two EMAs; .hist is the line minus its signal, crossing 0 flags momentum shifts.' },
  cci: { title: 'CCI', panel: 'osc', about: 'Distance of price from its average; beyond +-100 is an extreme.' },
  williams: { title: 'Williams %R', panel: 'osc', about: 'Like stochastic, from -100 (oversold) to 0 (overbought).' },
  adx: { title: 'ADX', panel: 'osc', about: 'Trend strength (not direction); .pdi / .mdi are the up / down pressure lines.' },
  atr: { title: 'ATR', panel: 'osc', about: 'Average candle size: how volatile the market is right now.' },
  roc: { title: 'Rate of change', panel: 'osc', about: 'Percent change of close over N candles.' },
  heikin: { title: 'Heikin-Ashi', panel: 'none', about: 'Smoothed candles; use its fields (e.g. ha.green, ha.nolow) in conditions. Not drawn on the chart.' },
  structure: { title: 'Market structure', panel: 'price', about: 'Swing highs and lows: .trend is +1 in an up-trend (higher highs and higher lows), -1 in a down-trend (lower highs and lower lows, or the last higher low broke after a lower high), 0 when unclear; .age = candles since it last changed (small = fresh shift); .sh / .sl = newest swing high / low (drawn on the chart); .hh / .hl = +1 higher, -1 lower than the swing before; .lead = what the newest swing says, at once: +1 after a higher high or higher low, -1 after a lower high or lower low (so HH, HL, then a lower high already reads -1). A reversal needs proof: after an up-trend the first lower low does not flip it down until a candle closes below that low (mirror for higher highs).' },
  engulf: { title: 'Engulfing-candle zones', panel: 'price', about: 'Demand zone = the red candle that a green candle (or a run of up to max_run green candles) engulfs in an up-trend (higher highs and lows); supply zone = the green candle engulfed by red in a down-trend. The zone is that candle\'s high-low range and works only the FIRST time price comes back (visits sets how many). dz_* / sz_* describe the zone price is trading into on this candle: lo, hi, visit (1 = first time back), age, body (size of the engulfing move in ATRs), run (candles that engulfed it); room_up / room_dn = ATRs to the next opposing zone.' },
  weak: { title: 'Weak high / weak low', panel: 'price', about: 'A shooting star (long upper wick, any colour) makes its high a weak high; a hammer (long lower wick) makes its low a weak low. Then it follows price toward that level: hi_* / lo_* = lvl (the level), new (1 on the star / hammer candle), run (candles since), touch (1 once price traded up to / down to the level, wicks count), closes (candles in a row that CLOSED beyond the level), dist (ATRs between the close and the level, negative = already beyond it). The setup ends after max_wait candles or when a candle closes past the star\'s low / hammer\'s high. The armed level is drawn on the chart as a dashed line.' },
  zones: { title: 'Supply/Demand zones', panel: 'price', about: 'Finds zones (a tight base followed by a strong departure) and follows them: dz_* = the demand zone price just touched, sz_* = supply; tests = visits so far (fresh zones are strong), move = how explosive the departure was, flip = 1 if the zone changed role after being broken, room_up / room_dn = ATRs to the next opposing zone; tight = base range in ATRs (small = compressed), dep = departure candle body ratio (1 = decisive), run = how far price has travelled away from the zone in ATRs (strength measured from the candles; this feed has no usable volume). The chart draws the zone lines for the zone just touched.' },
};

const ID_BASE: Record<string, string> = { bollinger: 'bb', stoch: 'stoch', macd: 'macd', adx: 'adx', supertrend: 'st', psar: 'psar', donchian: 'dc', heikin: 'ha', williams: 'wpr', zones: 'zn', structure: 'ms', engulf: 'eg', weak: 'wk' };
export function suggestId(type: string, params: Record<string, number>, taken: Set<string>, reserved: Set<string>): string {
  const n = params.n ?? params.k;
  const base = ID_BASE[type] ?? `${type}${n ?? ''}`;
  let id = base, i = 2;
  while (taken.has(id) || reserved.has(id)) id = `${base}_${i++}`;
  return id;
}

export interface Need { key: string; type: string; params: Record<string, number> }
export interface Recipe { id: string; title: string; about: string; needs: Need[]; build: (id: Record<string, string>) => { call: Clause[]; put: Clause[] } }
const I = (key: string, type: string, params: Record<string, number>): Need => ({ key, type, params });

export const RECIPES: Recipe[] = [
  { id: 'rsi-extremes', title: 'RSI oversold / overbought', about: 'CALL when RSI is below 30, PUT when it is above 70 (mean reversion).', needs: [I('r', 'rsi', { n: 14 })],
    build: (x) => ({ call: [cmp(ref(x.r), '<', num(30))], put: [cmp(ref(x.r), '>', num(70))] }) },
  { id: 'rsi-turn', title: 'RSI turns back from an extreme', about: 'CALL when RSI crosses back above 30, PUT when it crosses back below 70.', needs: [I('r', 'rsi', { n: 14 })],
    build: (x) => ({ call: [cmp(ref(x.r), 'cross_above', num(30))], put: [cmp(ref(x.r), 'cross_below', num(70))] }) },
  { id: 'price-ema', title: 'Price above / below EMA 50', about: 'Trend following: CALL while price is above the EMA, PUT while below.', needs: [I('e', 'ema', { n: 50 })],
    build: (x) => ({ call: [cmp(ref('close'), '>', ref(x.e))], put: [cmp(ref('close'), '<', ref(x.e))] }) },
  { id: 'ema-cross', title: 'Fast EMA crosses slow EMA', about: 'CALL when EMA 9 crosses above EMA 21, PUT when it crosses below.', needs: [I('f', 'ema', { n: 9 }), I('s', 'ema', { n: 21 })],
    build: (x) => ({ call: [cmp(ref(x.f), 'cross_above', ref(x.s))], put: [cmp(ref(x.f), 'cross_below', ref(x.s))] }) },
  { id: 'macd-zero', title: 'MACD histogram crosses zero', about: 'Momentum shift: CALL when the histogram turns positive, PUT when it turns negative.', needs: [I('m', 'macd', { fast: 12, slow: 26, signal: 9 })],
    build: (x) => ({ call: [cmp(ref(`${x.m}.hist`), 'cross_above', num(0))], put: [cmp(ref(`${x.m}.hist`), 'cross_below', num(0))] }) },
  { id: 'bb-reentry', title: 'Bollinger band re-entry', about: 'Price closed outside a band and just came back inside: CALL at the lower band, PUT at the upper.', needs: [I('b', 'bollinger', { n: 20, k: 2 })],
    build: (x) => ({ call: [cmp(ref('close', 1), '<', ref(`${x.b}.lower`, 1)), cmp(ref('close'), '>=', ref(`${x.b}.lower`))],
      put: [cmp(ref('close', 1), '>', ref(`${x.b}.upper`, 1)), cmp(ref('close'), '<=', ref(`${x.b}.upper`))] }) },
  { id: 'stoch-extreme', title: 'Stochastic cross in an extreme', about: 'k crosses d while oversold (CALL) or overbought (PUT).', needs: [I('s', 'stoch', { k: 14, smooth: 3, d: 3 })],
    build: (x) => ({ call: [cmp(ref(`${x.s}.k`), 'cross_above', ref(`${x.s}.d`)), cmp(ref(`${x.s}.k`), '<', num(20))],
      put: [cmp(ref(`${x.s}.k`), 'cross_below', ref(`${x.s}.d`)), cmp(ref(`${x.s}.k`), '>', num(80))] }) },
  { id: 'supertrend-flip', title: 'Supertrend flips', about: 'CALL when the trend line turns up, PUT when it turns down.', needs: [I('t', 'supertrend', { n: 10, mult: 3 })],
    build: (x) => ({ call: [cmp(ref(`${x.t}.dir`), '>', num(0)), cmp(ref(`${x.t}.dir`, 1), '<', num(0))],
      put: [cmp(ref(`${x.t}.dir`), '<', num(0)), cmp(ref(`${x.t}.dir`, 1), '>', num(0))] }) },
  { id: 'engulfing', title: 'Engulfing candle', about: 'A candle that swallows the previous one: bullish = CALL, bearish = PUT.', needs: [],
    build: () => ({ call: [cmp(ref('bull_engulf'), 'is')], put: [cmp(ref('bear_engulf'), 'is')] }) },
  { id: 'hammer-star', title: 'Hammer / shooting star', about: 'Long-wick reversal candles: hammer = CALL, shooting star = PUT.', needs: [],
    build: () => ({ call: [cmp(ref('hammer'), 'is')], put: [cmp(ref('star'), 'is')] }) },
];
