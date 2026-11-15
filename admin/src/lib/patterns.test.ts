import { describe, expect, it } from 'vitest';
import { mirror, PATTERN_RECIPES, patternProblems, rel, step } from './patterns';
import { formToSpec, specToForm } from './specModel';

describe('patterns', () => {
  it("mirror of the hammer-sweep BUY pattern is the SELL pattern (shooting star, lower lows, green sweep)", () => {
    const r = PATTERN_RECIPES.find((x) => x.id === 'hammer-sweep')!;
    const put = r.put!.steps;
    expect(put.map((s) => [s.color, s.shape])).toEqual([['red', 'any'], ['red', 'star'], ['green', 'any']]);
    expect(put[0].rels).toEqual([rel('low', '<', 'prev', 'low')]);
    expect(put[1].rels).toEqual([rel('high', '>', 'prev', 'high')]);
    expect(put[2].rels).toEqual([rel('high', '>', 1, 'high', 'any')]);
    expect(put[0].label).toBe('Downtrend');
    expect(put[2].label).toBe('Sweep above the shooting star');
    expect(mirror(mirror(r.call))).toEqual(r.call);                         // an involution
  });
  it('size comparisons are not flipped by mirroring', () => {
    const e = PATTERN_RECIPES.find((x) => x.id === 'engulfing')!;
    expect(e.put!.steps[1].rels.find((x) => x.prop === 'body')!.op).toBe('>');
  });
  it('every recipe is valid on both sides', () => {
    for (const r of PATTERN_RECIPES) { expect(patternProblems(r.call), r.id).toEqual([]); expect(patternProblems(r.put!), r.id).toEqual([]); }
  });
  it('flags forward references and oversized repeat ranges', () => {
    expect(patternProblems({ steps: [step('a', 'any', 'any', 1, 1, [rel('low', '<', 0, 'low')])] }).length).toBe(1);
    expect(patternProblems({ steps: [step('a', 'green', 'any', 1, 8), step('b', 'green', 'any', 1, 8), step('c', 'green', 'any', 1, 8), step('d', 'any', 'any', 1, 8)] })[0]).toMatch(/Too many/);
  });
  it('a pattern factor round-trips through the spec form', () => {
    const r = PATTERN_RECIPES[0];
    const spec = { engine: 'spec/1', kind: 'factors', timeframes: [60], scope: {}, indicators: [], gates: { min_candles: 60 }, filters: [],
      factors: [{ id: 'hs', name: 'Hammer sweep', weight: 100, kind: 'pattern', call: r.call, put: r.put, vote: 1 }] };
    const back = formToSpec(specToForm(spec));
    expect(back.factors[0]).toEqual({ id: 'hs', name: 'Hammer sweep', weight: 100, kind: 'pattern', call: r.call, put: r.put, vote: 1 });
  });
});
