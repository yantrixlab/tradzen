import { describe, expect, it } from 'vitest';
import { clauseText, describeGroup, parse, RECIPES, serialize, suggestId } from './conditions';
import type { IndicatorForm } from './specModel';

const rt = (t: string) => serialize(parse(t));
const inds: IndicatorForm[] = [{ id: 'rsi14', type: 'rsi', params: { n: 14 } }, { id: 'bb', type: 'bollinger', params: { n: 20, k: 2 } }];

const REAL = [
  'close[1] < bb.lower[1] and close >= bb.lower', 'cross_above(rsi14, 30)', 'rsi2 < 10 and close > sma200', 'cross_above(st.k, st.d) and st.k < 25',
  'cross_above(ema9, ema21) and ema50 > ema50[3]', 'st.dir == 1 and st.dir[1] == -1', 'sar.dir == 1 and sar.dir[1] == -1 and close > ema200',
  'adx14.adx > 25 and cross_above(adx14.pdi, adx14.mdi)', 'ha.green and ha.green[1] and ha.nolow', '(bull_engulf or hammer) and low <= lowest(low, 20)[1] + 0.3 * atr14',
  'close > don.upper[1]', 'bb.pctile[1] <= 20 and close > bb.upper', 'ema21 > ema50 and rsi14 < 50 and cross_above(st.k, st.d) and st.k < 50', 'streak <= -4',
];

describe('conditions', () => {
  it('round-trips every condition used by the built-in strategies byte for byte', () => {
    for (const t of REAL) expect(rt(t), t).toBe(t);
  });
  it('round-trips the common shapes unchanged', () => {
    for (const t of ['rsi14 < 30', 'rsi14 < 30 and close > ema200', 'cross_above(macd.hist, 0)', 'close[1] < bb.lower[1] and close >= bb.lower',
      'bull_engulf', 'not doji', 'rising(close, 3)', 'a1 > 1 or b1 < 2.5']) expect(rt(t)).toBe(t);
  });
  it('turns recognised parts into sentence clauses and the rest into custom clauses', () => {
    const g = parse('(bull_engulf or hammer) and low <= lowest(low, 20)[1]');
    expect(g.mode).toBe('and');
    expect(g.clauses.map((c) => c.raw !== undefined)).toEqual([true, true]);       // neither is expressible as a simple sentence
    expect(serialize(g)).toBe('(bull_engulf or hammer) and low <= lowest(low, 20)[1]');
    const h = parse('rsi14 < 30 and close > ema200');
    expect(h.clauses.every((c) => c.raw === undefined)).toBe(true);
  });
  it('keeps mixed top-level and/or as one custom clause instead of changing its meaning', () => {
    const t = 'a1 > 1 and b1 < 2 or c1 > 3';
    const g = parse(t);
    expect(g.clauses).toHaveLength(1);
    expect(serialize(g)).toBe(t);
  });
  it('wraps custom clauses containing and/or when joined', () => {
    const g = parse('rsi14 < 30 and close > ema200');
    g.clauses.push({ id: 'x', raw: 'hammer or star', left: null, op: '>', right: null });
    expect(serialize(g)).toBe('rsi14 < 30 and close > ema200 and (hammer or star)');
  });
  it('skips incomplete clauses and handles an empty group', () => {
    expect(serialize(parse(''))).toBe('');
    const g = parse('rsi14 < 30');
    g.clauses.push({ id: 'y', left: null, op: '>', right: null });
    expect(serialize(g)).toBe('rsi14 < 30');
    expect(clauseText(g.clauses[1])).toBeNull();
  });
  it('describes in plain English', () => {
    expect(describeGroup(parse('rsi14 < 30 and close[1] > bb.upper'), inds)).toBe('RSI(14) is below 30  AND  close price (1 candle ago) is above Bollinger(20,2) upper');
  });
  it('every recipe builds parsable text for both sides', () => {
    for (const r of RECIPES) {
      const ids: Record<string, string> = {};
      r.needs.forEach((n) => { ids[n.key] = n.type === 'macd' ? 'macd' : `${n.type}x`; });
      const { call, put } = r.build(ids);
      for (const side of [call, put]) {
        const text = serialize({ mode: 'and', clauses: side });
        expect(text.length, r.id).toBeGreaterThan(0);
        expect(rt(text), r.id).toBe(text);
      }
    }
  });
  it('suggests unique ids that do not clash with built-in names', () => {
    expect(suggestId('rsi', { n: 14 }, new Set(), new Set())).toBe('rsi14');
    expect(suggestId('rsi', { n: 14 }, new Set(['rsi14']), new Set())).toBe('rsi14_2');
    expect(suggestId('bollinger', { n: 20, k: 2 }, new Set(), new Set(['bb']))).toBe('bb_2');
  });
});
