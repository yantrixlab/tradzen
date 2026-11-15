import type { PatternForm } from './patterns';

// Form model of a strategy spec <-> the JSON spec the backend validates. Pure functions (unit-tested in specModel.test.ts).
export interface IndicatorForm { id: string; type: string; params: Record<string, number> }
export interface FactorForm { id: string; name: string; weight: number; kind: 'expr' | 'fn' | 'pattern'; call: string; put: string; vote: number; fn: string; params: Record<string, number>; callPat: PatternForm | null; putPat: PatternForm | null }
export interface FilterForm { type: string; [k: string]: unknown }
export interface SpecForm {
  kind: 'factors' | 'meta';
  timeframes: number[];
  market: '' | 'OTC' | 'LIVE';
  symbols: string;
  indicators: IndicatorForm[];
  factors: FactorForm[];
  gates: { min_candles: number; min_agree: number; max_opposing_weight: number; min_strength: number; tierA: boolean; tier_a_agree: number; tier_a_strength: number };
  filters: FilterForm[];
  consensus: { members: string; min_agree: number };
}

export const blankFactor = (n = 1): FactorForm => ({ id: `factor${n}`, name: `Factor ${n}`, weight: 100, kind: 'expr', call: '', put: '', vote: 1, fn: '', params: {}, callPat: null, putPat: null });

export const blankForm = (): SpecForm => ({
  kind: 'factors', timeframes: [60, 300, 900], market: '', symbols: '', indicators: [], factors: [blankFactor()],
  gates: { min_candles: 60, min_agree: 1, max_opposing_weight: 100, min_strength: 0, tierA: false, tier_a_agree: 3, tier_a_strength: 40 },
  filters: [], consensus: { members: 'all_active', min_agree: 3 },
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;

export function specToForm(spec: Json): SpecForm {
  const f = blankForm();
  f.kind = spec.kind === 'meta' ? 'meta' : 'factors';
  f.timeframes = spec.timeframes ?? f.timeframes;
  f.market = spec.scope?.market ?? '';
  f.symbols = (spec.scope?.symbols ?? []).join(', ');
  f.indicators = (spec.indicators ?? []).map((i: Json) => {
    const { id, type, ...params } = i;
    return { id, type, params };
  });
  f.factors = (spec.factors ?? []).map((x: Json): FactorForm => ({
    id: x.id, name: x.name ?? x.id, weight: x.weight ?? 100, kind: x.kind === 'fn' ? 'fn' : x.kind === 'pattern' ? 'pattern' : 'expr',
    call: x.kind === 'pattern' ? '' : x.call ?? '', put: x.kind === 'pattern' ? '' : x.put ?? '',
    vote: x.vote ?? 1, fn: x.fn ?? '', params: { ...(x.params ?? {}) },
    callPat: x.kind === 'pattern' && x.call ? JSON.parse(JSON.stringify(x.call)) : null, putPat: x.kind === 'pattern' && x.put ? JSON.parse(JSON.stringify(x.put)) : null,
  }));
  if (!f.factors.length && f.kind === 'factors') f.factors = [blankFactor()];
  const g = spec.gates ?? {};
  f.gates = {
    min_candles: g.min_candles ?? 60, min_agree: g.min_agree ?? 1, max_opposing_weight: g.max_opposing_weight ?? 100, min_strength: g.min_strength ?? 0,
    tierA: !!g.tier_a, tier_a_agree: g.tier_a?.agree ?? 3, tier_a_strength: g.tier_a?.strength ?? 40,
  };
  f.filters = (spec.filters ?? []).map((x: Json) => ({ ...x }));
  if (spec.consensus) f.consensus = { members: spec.consensus.members === 'all_active' ? 'all_active' : (spec.consensus.members ?? []).join(', '), min_agree: spec.consensus.min_agree ?? 3 };
  return f;
}

const csv = (s: string): string[] => s.split(',').map((x) => x.trim()).filter(Boolean);

export function formToSpec(f: SpecForm): Json {
  const scope: Json = { symbols: csv(f.symbols).length ? csv(f.symbols) : null, market: f.market || null };
  const base: Json = { engine: 'spec/1', kind: f.kind, timeframes: [...f.timeframes].sort((a, b) => a - b), scope };
  const gates: Json = {
    min_candles: Number(f.gates.min_candles), min_agree: Number(f.gates.min_agree), max_opposing_weight: Number(f.gates.max_opposing_weight),
    min_strength: Number(f.gates.min_strength), tier_a: f.gates.tierA ? { agree: Number(f.gates.tier_a_agree), strength: Number(f.gates.tier_a_strength) } : null,
  };
  if (f.kind === 'meta') {
    const members = f.consensus.members.trim() === 'all_active' || !f.consensus.members.trim() ? 'all_active' : csv(f.consensus.members);
    return { ...base, gates, consensus: { members, min_agree: Number(f.consensus.min_agree) } };
  }
  return {
    ...base, gates, expiry_candles: 1,
    indicators: f.indicators.map((i) => ({ id: i.id, type: i.type, ...i.params })),
    factors: f.factors.map((x) => {
      const out: Json = { id: x.id, name: x.name, weight: Number(x.weight), kind: x.kind };
      if (x.kind === 'fn') {
        out.fn = x.fn;
        out.params = x.params;
      } else if (x.kind === 'pattern') {
        if (x.callPat && x.callPat.steps.length) out.call = x.callPat;
        if (x.putPat && x.putPat.steps.length) out.put = x.putPat;
        out.vote = Number(x.vote);
      } else {
        if (x.call.trim()) out.call = x.call.trim();
        if (x.put.trim()) out.put = x.put.trim();
        out.vote = Number(x.vote);
      }
      return out;
    }),
    filters: f.filters.map((x) => ({ ...x })),
  };
}
