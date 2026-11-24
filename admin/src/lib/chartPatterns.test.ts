import { describe, expect, it } from 'vitest';
import { completedAt, contextText, countFound, detect, PATTERNS, patternDef, pivots, SECTIONS, structureAt, type PCandle } from './chartPatterns';

let T = 1_000_000;
const mk = (o: number, c: number, h?: number, l?: number): PCandle => ({ t: (T += 60), o, c, h: h ?? Math.max(o, c) + 0.3, l: l ?? Math.min(o, c) - 0.3 });
/** 20 ordinary candles (range about 2, body about 1) so the patterns have something normal to be measured against */
const base = (): PCandle[] => { T = 1_000_000; const out: PCandle[] = []; for (let i = 0; i < 20; i++) out.push(i % 2 ? mk(100.4, 100, 101, 99.4) : mk(100, 100.4, 101, 99.4)); return out; };
/** a steady fall (or rise) of n candles starting from `from` */
const drift = (from: number, step: number, n: number): PCandle[] => Array.from({ length: n }, (_, k) => mk(from + k * step, from + (k + 1) * step, Math.max(from + k * step, from + (k + 1) * step) + 0.4, Math.min(from + k * step, from + (k + 1) * step) - 0.4));
const ids = (cs: PCandle[], want: string[]) => detect(cs, want).map((d) => d.id);

describe('catalogue', () => {
  it('has unique ids, a candle test for every candle pattern and sections that exist', () => {
    expect(new Set(PATTERNS.map((p) => p.id)).size).toBe(PATTERNS.length);
    for (const p of PATTERNS) {
      expect(SECTIONS[p.group]).toContain(p.section);
      expect(p.about.length).toBeGreaterThan(20);
      expect(p.group === 'candle' ? !!p.sketch.candles?.length : !!p.sketch.path?.length).toBe(true);
    }
    expect(PATTERNS.filter((p) => p.group === 'candle').length).toBeGreaterThanOrEqual(30);
    expect(PATTERNS.filter((p) => p.group === 'chart').length).toBeGreaterThanOrEqual(14);
    expect(patternDef('hammer')?.bias).toBe('bull');
  });
  it('finds nothing on flat or too-short data and never throws', () => {
    expect(detect([])).toEqual([]);
    expect(detect([mk(1, 1)])).toEqual([]);
    const flat = Array.from({ length: 60 }, () => mk(5, 5, 5, 5));
    expect(detect(flat)).toEqual([]);
  });
});

describe('candle patterns', () => {
  it('bullish engulfing after a fall, and only after a fall', () => {
    const cs = [...base(), ...drift(100, -0.8, 6), mk(95.2, 94.7, 95.4, 94.5), mk(94.5, 96.2, 96.4, 94.3)];
    const hit = detect(cs, ['bullengulf']);
    expect(hit).toHaveLength(1);
    expect(hit[0]).toMatchObject({ bias: 'bull', at: cs.length - 1, i0: cs.length - 2 });
    const rose = [...base(), ...drift(100, 0.8, 6), mk(104.8, 104.3, 105, 104.1), mk(104.1, 105.8, 106, 103.9)];
    expect(detect(rose, ['bullengulf']).some((d) => d.at === rose.length - 1)).toBe(false);          // after a clear rise it is not a reversal
    const flatLead = [...base(), mk(95.2, 94.7, 95.4, 94.5), mk(94.5, 96.2, 96.4, 94.3)];
    expect(detect(flatLead, ['bullengulf']).some((d) => d.at === flatLead.length - 1)).toBe(true);                  // with no clear move before it, the shape is still reported
  });
  it('hammer after a fall is bullish, the same shape after a rise is a hanging man', () => {
    const shape = (c0: number) => mk(c0, c0 + 0.3, c0 + 0.4, c0 - 2.4);
    const down = [...base(), ...drift(100, -0.8, 6), shape(95.2)];
    expect(ids(down, ['hammer', 'hangman'])).toEqual(['hammer']);
    const upc = [...base(), ...drift(100, 0.8, 6), shape(104.8)];
    expect(ids(upc, ['hammer', 'hangman'])).toEqual(['hangman']);
  });
  it('a hammer is found even when no clear fall came before it', () => {
    const flatHammer = [...base(), mk(100, 100.4, 100.6, 97.6)];
    expect(ids(flatHammer, ['hammer', 'hangman'])).toEqual(['hammer']);
    const flatStar = [...base(), mk(100.4, 100, 103.2, 99.8)];
    expect(ids(flatStar, ['invhammer', 'shootstar'])).toEqual(['invhammer']);
    const slightlyWeak = [...base(), mk(100, 100.9, 101.1, 97.7)];                   // body a third of the range, lower wick twice the body
    expect(ids(slightlyWeak, ['hammer'])).toEqual(['hammer']);
  });
  it('every hammer-shaped candle is a hammer or a hanging man, never neither', () => {
    for (const lead of [-1, -0.3, 0, 0.3, 1]) {
      const cs = [...base(), ...drift(100, lead * 0.8, 6), mk(100 + lead * 4.8, 100.3 + lead * 4.8, 100.5 + lead * 4.8, 97.6 + lead * 4.8)];
      expect(detect(cs, ['hammer', 'hangman']).filter((d) => d.at === cs.length - 1)).toHaveLength(1);
    }
  });
  it('doji, dragonfly and gravestone', () => {
    const d = [...base(), mk(100, 100.05, 101, 99)];
    expect(ids(d, ['doji', 'dragonfly', 'gravestone', 'longleg'])).toContain('doji');
    const dr = [...base(), mk(100, 100.02, 100.04, 98)];
    expect(ids(dr, ['dragonfly', 'gravestone'])).toEqual(['dragonfly']);
    const gr = [...base(), mk(100, 99.98, 102, 99.96)];
    expect(ids(gr, ['dragonfly', 'gravestone'])).toEqual(['gravestone']);
  });
  it('marubozu and the big candle', () => {
    const m = [...base(), mk(100, 103, 103.05, 99.95)];
    expect(ids(m, ['bullmaru', 'bearmaru', 'biggreen']).sort()).toEqual(['biggreen', 'bullmaru']);
  });
  it('three white soldiers and three black crows', () => {
    const s = [...base(), mk(100, 101.6, 101.8, 99.9), mk(101, 102.8, 103, 100.9), mk(102.2, 104, 104.2, 102.1)];
    expect(ids(s, ['soldiers', 'crows'])).toEqual(['soldiers']);
    const c = [...base(), mk(104, 102.4, 104.1, 102.2), mk(103, 101.2, 103.1, 101), mk(101.8, 100, 101.9, 99.8)];
    expect(ids(c, ['soldiers', 'crows'])).toEqual(['crows']);
  });
  it('morning star after a fall and evening star after a rise', () => {
    const m = [...base(), ...drift(100, -0.8, 6), mk(95.2, 92, 95.4, 91.8), mk(91.8, 91.5, 92.2, 91.2), mk(92, 94.8, 95, 91.9)];
    expect(ids(m, ['morningstar', 'eveningstar'])).toEqual(['morningstar']);
    const e = [...base(), ...drift(100, 0.8, 6), mk(104.8, 108, 108.2, 104.6), mk(108.2, 108.6, 109, 107.8), mk(108.3, 105, 108.5, 104.8)];
    expect(ids(e, ['morningstar', 'eveningstar'])).toEqual(['eveningstar']);
  });
  it('inside and outside bars', () => {
    const i = [...base(), mk(99, 102, 103, 98), mk(100, 101, 101.5, 99.5)];
    const last = (cs: PCandle[], id: string) => detect(cs, [id]).some((d) => d.at === cs.length - 1);
    expect(last(i, 'insidebar')).toBe(true); expect(last(i, 'outsidebar')).toBe(false);
    const o = [...base(), mk(100, 100.6, 101, 99.8), mk(99.5, 102.5, 103.5, 98.5)];
    expect(last(o, 'outsidebar')).toBe(true); expect(last(o, 'insidebar')).toBe(false);
  });
  it('rising three methods', () => {
    const cs = [...base(), mk(100, 103, 103.2, 99.9), mk(102.6, 102, 102.8, 101.6), mk(102, 101.5, 102.2, 101.2), mk(101.5, 101.2, 101.7, 101), mk(101.3, 104.4, 104.6, 101.2)];
    expect(ids(cs, ['rising3', 'falling3'])).toEqual(['rising3']);
  });
  it('a candle that was filled in because data was missing never takes part', () => {
    const cs = [...base(), ...drift(100, -0.8, 6), mk(95.2, 94.7, 95.4, 94.5), { ...mk(94.5, 96.2, 96.4, 94.3), syn: true }];
    expect(ids(cs, ['bullengulf'])).toEqual([]);
  });
});

/** a price path through (candle index, price) points: every candle moves along it, with a small wick */
function walk(points: [number, number][], from = 20): PCandle[] {
  const cs = base();
  let prev = points[0][1];
  for (let i = points[0][0]; i <= points[points.length - 1][0]; i++) {
    let k = 0; while (k < points.length - 2 && i > points[k + 1][0]) k++;
    const [i0, p0] = points[k], [i1, p1] = points[k + 1];
    const p = p0 + ((p1 - p0) * (i - i0)) / (i1 - i0);
    cs.push(mk(prev, p, Math.max(prev, p) + 0.15, Math.min(prev, p) - 0.15));
    prev = p;
  }
  void from;
  return cs;
}

describe('swing points', () => {
  it('alternate between highs and lows', () => {
    const cs = walk([[0, 100], [8, 110], [16, 102], [24, 111], [32, 100]]);
    const pv = pivots(cs);
    expect(pv.map((p) => p.hi)).toEqual([true, false, true]);
    for (let i = 1; i < pv.length; i++) expect(pv[i].hi).not.toBe(pv[i - 1].hi);
  });
});

describe('chart patterns', () => {
  it('double top: two equal highs with a dip between, completed when the second high is confirmed', () => {
    const cs = walk([[0, 100], [10, 110], [20, 102], [30, 110.2], [40, 101]]);
    const hit = detect(cs, ['dtop', 'dbottom']);
    expect(hit.map((d) => d.id)).toEqual(['dtop']);
    expect(hit[0].bias).toBe('bear');
    expect(hit[0].lines.length).toBeGreaterThan(0);
    expect(hit[0].at).toBeGreaterThan(hit[0].i1);
  });
  it('double bottom is its mirror', () => {
    const cs = walk([[0, 110], [10, 100], [20, 108], [30, 99.8], [40, 109]]);
    expect(detect(cs, ['dtop', 'dbottom']).map((d) => d.id)).toEqual(['dbottom']);
  });
  it('highs that differ a lot are not a double top', () => {
    const cs = walk([[0, 100], [10, 110], [20, 102], [30, 116], [40, 101]]);
    expect(ids(cs, ['dtop'])).toEqual([]);
  });
  it('head and shoulders and its inverse', () => {
    const cs = walk([[0, 92], [8, 108], [16, 102], [24, 114], [32, 102], [40, 108.2], [48, 100]]);
    expect(ids(cs, ['hns', 'ihns', 'dtop'])).toContain('hns');
    const inv = walk([[0, 128], [8, 112], [16, 118], [24, 106], [32, 118], [40, 112.2], [48, 120]]);
    expect(ids(inv, ['hns', 'ihns'])).toEqual(['ihns']);
  });
  it('triple top', () => {
    const cs = walk([[0, 100], [8, 110], [16, 102], [24, 110.2], [32, 102.2], [40, 109.8], [48, 100]]);
    expect(ids(cs, ['ttop'])).toEqual(['ttop']);
  });
  it('ascending triangle: flat highs, rising lows', () => {
    const cs = walk([[0, 100], [8, 112], [16, 102], [24, 112.2], [32, 106], [40, 112], [48, 109]]);
    expect(ids(cs, ['asctri', 'desctri', 'symtri', 'rect'])).toContain('asctri');
  });
  it('descending triangle: flat lows, falling highs', () => {
    const cs = walk([[0, 120], [8, 108], [16, 118], [24, 108.2], [32, 114], [40, 108], [48, 111]]);
    expect(ids(cs, ['asctri', 'desctri', 'symtri', 'rect'])).toContain('desctri');
  });
  it('symmetrical triangle: lower highs, higher lows', () => {
    const cs = walk([[0, 105], [8, 120], [16, 100], [24, 116], [32, 104], [40, 112], [48, 107]]);
    expect(ids(cs, ['symtri', 'asctri', 'desctri'])).toContain('symtri');
  });
  it('rectangle: flat highs and flat lows', () => {
    const cs = walk([[0, 105], [8, 115], [16, 100], [24, 115.2], [32, 100.2], [40, 115], [48, 105]]);
    expect(ids(cs, ['rect', 'symtri'])).toContain('rect');
  });
  it('rising and falling wedges narrow in the direction of the move', () => {
    const rw = walk([[0, 100], [8, 106], [16, 102], [24, 110], [32, 107], [40, 112], [48, 110]]);
    expect(ids(rw, ['risewedge', 'fallwedge'])).toContain('risewedge');
    const fw = walk([[0, 120], [8, 114], [16, 118], [24, 110], [32, 113], [40, 108], [48, 110]]);
    expect(ids(fw, ['risewedge', 'fallwedge'])).toContain('fallwedge');
  });
  it('rising and falling channels run in parallel', () => {
    const up = walk([[0, 100], [8, 108], [16, 104], [24, 112], [32, 108], [40, 116], [48, 112]]);
    expect(ids(up, ['upchannel', 'downchannel'])).toContain('upchannel');
    const dn = walk([[0, 120], [8, 112], [16, 116], [24, 108], [32, 112], [40, 104], [48, 108]]);
    expect(ids(dn, ['upchannel', 'downchannel'])).toContain('downchannel');
  });
  it('bull flag: a sharp rise, then a small drifting pullback', () => {
    const cs = walk([[0, 92], [9, 112], [16, 106], [22, 110.5], [30, 104], [36, 108]]);
    expect(ids(cs, ['bullflag', 'bearflag'])).toContain('bullflag');
    const bear = walk([[0, 128], [9, 108], [16, 114], [22, 109.5], [30, 116], [36, 112]]);
    expect(ids(bear, ['bullflag', 'bearflag'])).toContain('bearflag');
  });
  it('reports a stretch once, not at every swing', () => {
    const cs = walk([[0, 100], [10, 110], [20, 102], [30, 110.2], [40, 101]]);
    expect(detect(cs, ['dtop'])).toHaveLength(1);
  });
});

describe('market structure', () => {
  const upWalk = () => walk([[0, 100], [8, 108], [14, 104], [22, 114], [28, 110], [36, 120], [42, 116]]);
  it('is an uptrend after higher highs and higher lows, a downtrend after lower ones, and sideways otherwise', () => {
    const cs = upWalk();
    const pv = pivots(cs);
    expect(structureAt(pv, cs.length, 2)).toBe('up');
    const dn = walk([[0, 120], [8, 112], [14, 116], [22, 106], [28, 110], [36, 100], [42, 104]]);
    expect(structureAt(pivots(dn), dn.length, 2)).toBe('down');
    const range = walk([[0, 105], [8, 115], [16, 100], [24, 115.1], [32, 100.1], [40, 115], [48, 105]]);
    expect(structureAt(pivots(range), range.length, 2)).toBe('side');
    expect(structureAt([], 50, 2)).toBe('side');
  });
  it('is attached to every detection and explained in words', () => {
    const cs = upWalk();
    const last = cs[cs.length - 1];
    cs.push(mk(last.c, last.c + 0.3, last.c + 0.4, last.c - 2.4));                     // a hammer-shaped candle at the end of the climb
    const d = detect(cs, ['hammer', 'hangman']).filter((x) => x.at === cs.length - 1);
    expect(d).toHaveLength(1);
    expect(d[0].structure).toBe('up');
    expect(contextText({ bias: 'bull', structure: 'down' })).toContain('possible reversal up');
    expect(contextText({ bias: 'bear', structure: 'up' })).toContain('possible reversal down');
    expect(contextText({ bias: 'bull', structure: 'up' })).toContain('with the trend');
    expect(contextText({ bias: 'bull', structure: 'side' })).toContain('range');
  });
  it('a double top needs a move up into it: the same shape inside a range is not one', () => {
    const rangey = walk([[0, 104], [6, 110], [12, 102], [18, 110.2], [24, 101], [30, 105]]);
    expect(ids(rangey, ['dtop'])).toEqual([]);
  });
});

describe('helpers', () => {
  it('counts the hits per pattern and finds those completed by a given candle', () => {
    const cs = [...base(), ...drift(100, -0.8, 6), mk(95.2, 94.7, 95.4, 94.5), mk(94.5, 96.2, 96.4, 94.3)];
    const ds = detect(cs);
    expect(countFound(ds).get('bullengulf')).toBe(1);
    expect(completedAt(ds, cs[cs.length - 1].t).some((d) => d.id === 'bullengulf')).toBe(true);
    expect(completedAt(ds, 5)).toEqual([]);
  });
});
