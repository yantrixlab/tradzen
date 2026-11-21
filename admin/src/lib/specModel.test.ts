import { describe, expect, it } from 'vitest';
import { blankForm, formToSpec, specToForm } from './specModel';

const spec = {
  engine: 'spec/1', kind: 'factors', timeframes: [300, 60], scope: { symbols: ['EURUSD_otc'], market: 'OTC' },
  indicators: [{ id: 'bb', type: 'bollinger', n: 20, k: 2 }, { id: 'rsi14', type: 'rsi', n: 14 }],
  factors: [
    { id: 'a', name: 'Band re-entry', weight: 60, kind: 'expr', call: 'close[1] < bb.lower[1] and close >= bb.lower', put: 'close[1] > bb.upper[1]', vote: 1 },
    { id: 'rsi_divergence', name: 'RSI Divergence', weight: 40, kind: 'fn', fn: 'rsi_divergence', params: { n_rsi: 14, lookback: 45 } },
  ],
  gates: { min_candles: 60, min_agree: 2, max_opposing_weight: 25, min_strength: 30, tier_a: { agree: 3, strength: 40 } },
  filters: [{ type: 'adx_max', value: 30, n: 14 }, { type: 'hours', include: [], exclude: ['21:00-23:00'] }], expiry_candles: 1,
};

describe('specModel', () => {
  it('round-trips a full spec without losing anything the backend cares about', () => {
    const back = formToSpec(specToForm(spec));
    expect(back.timeframes).toEqual([60, 300]);
    expect(back.scope).toEqual({ symbols: ['EURUSD_otc'], market: 'OTC' });
    expect(back.indicators).toEqual(spec.indicators);
    expect(back.factors).toEqual(spec.factors);
    expect(back.gates).toEqual(spec.gates);
    expect(back.filters).toEqual(spec.filters);
  });

  it('keeps the early alert (heads_up) through the form and drops it when both conditions are empty', () => {
    const hu = { title: 'weak level ahead', put: 'wk.hi_lvl > 0', call: 'wk.lo_lvl > 0', level: { call: 'wk.lo_lvl', put: 'wk.hi_lvl' }, text: { put: 'Star at {wk.hi_lvl}', call: 'Hammer at {wk.lo_lvl}' } };
    const form = specToForm({ ...spec, heads_up: hu });
    expect(form.headsUp?.levelPut).toBe('wk.hi_lvl');
    expect(formToSpec(form).heads_up).toEqual(hu);
    form.headsUp = { ...form.headsUp!, call: '', put: '' };
    expect(formToSpec(form).heads_up).toBeUndefined();
    expect(formToSpec(specToForm(spec)).heads_up).toBeUndefined();
  });

  it('omits empty conditions and unused tier rule', () => {
    const f = blankForm();
    f.factors[0].call = 'close > open';
    const s = formToSpec(f);
    expect(s.factors[0]).toEqual({ id: 'factor1', name: 'Factor 1', weight: 100, kind: 'expr', call: 'close > open', vote: 1 });
    expect(s.gates.tier_a).toBeNull();
    expect(s.scope).toEqual({ symbols: null, market: null });
  });

  it('coerces text inputs to numbers', () => {
    const f = blankForm();
    f.gates.min_candles = '80' as unknown as number;
    f.factors[0].weight = '45' as unknown as number;
    const s = formToSpec(f);
    expect(s.gates.min_candles).toBe(80);
    expect(s.factors[0].weight).toBe(45);
  });

  it('handles meta (consensus) strategies', () => {
    const meta = { kind: 'meta', timeframes: [60], consensus: { members: ['a', 'b'], min_agree: 2 }, gates: {} };
    const f = specToForm(meta);
    expect(f.kind).toBe('meta');
    expect(f.consensus.members).toBe('a, b');
    expect(formToSpec(f).consensus).toEqual({ members: ['a', 'b'], min_agree: 2 });
    f.consensus.members = 'all_active';
    expect(formToSpec(f).consensus.members).toBe('all_active');
  });
});
