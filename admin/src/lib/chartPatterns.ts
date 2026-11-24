// Candlestick patterns (1-5 candles) and chart patterns (shapes made by the swing highs and lows) found on the candles of a chart, for the "Patterns" dialog.
// Pure functions: nothing here talks to the server. A detection says WHERE a pattern ended (`at`, the candle that completes it - the one an alert is raised on), which candles
// it covers and, for chart patterns, the lines that draw it. A pattern is a description of the shape, not a trading signal: a hit rate has to be measured before it is traded.

export interface PCandle { t: number; o: number; h: number; l: number; c: number; syn?: boolean }
export type Bias = 'bull' | 'bear' | 'neutral';
export type Group = 'candle' | 'chart';

/** A tiny drawing for the grid card. Candles: [open, close, high, low] on a 0-10 scale (10 = top). Chart patterns: a zig-zag of [x, y] points (0-10) and guide lines [x0, y0, x1, y1]. */
export interface Sketch { candles?: [number, number, number, number][]; path?: [number, number][]; guides?: [number, number, number, number][] }

export interface PatternDef {
  id: string; name: string; short: string; group: Group; bias: Bias; section: string; size: number; about: string; sketch: Sketch;
}

export type Structure = 'up' | 'down' | 'side';
export const STRUCTURE_WORD: Record<Structure, string> = { up: 'Uptrend', down: 'Downtrend', side: 'Sideways' };

export interface Detection {
  id: string; bias: Bias; structure: Structure; i0: number; i1: number; at: number; t0: number; t1: number; tAt: number; lo: number; hi: number;
  lines: { t0: number; p0: number; t1: number; p1: number; dash?: boolean }[];
}

export const BIAS_COLOR: Record<Bias, string> = { bull: '#22c55e', bear: '#ef4444', neutral: '#a78bfa' };
export const BIAS_WORD: Record<Bias, string> = { bull: 'Bullish', bear: 'Bearish', neutral: 'Neutral' };

/** What the market structure around a pattern says about it, in a few words: a bullish pattern at the low of a downtrend is a possible reversal, in an uptrend it goes with the trend, in a range it is weaker. */
export function contextText(d: { bias: Bias; structure: Structure }): string {
  const w = STRUCTURE_WORD[d.structure];
  if (d.structure === 'side') return `${w} - inside a range, weaker`;
  if (d.bias === 'neutral') return `${w} - indecision`;
  const with_ = (d.bias === 'bull') === (d.structure === 'up');
  return `${w} - ${with_ ? 'goes with the trend' : d.bias === 'bull' ? 'possible reversal up' : 'possible reversal down'}`;
}

const K = (o: number, c: number, h: number, l: number): [number, number, number, number] => [o, c, h, l];

// ------------------------------------------------------------------ the catalogue
const P = (group: Group, id: string, name: string, short: string, bias: Bias, section: string, size: number, about: string, sketch: Sketch): PatternDef => ({ id, name, short, group, bias, section, size, about, sketch });
const CD = (id: string, name: string, short: string, bias: Bias, section: string, size: number, about: string, candles: [number, number, number, number][]) => P('candle', id, name, short, bias, section, size, about, { candles });
const CH = (id: string, name: string, short: string, bias: Bias, section: string, about: string, path: [number, number][], guides: [number, number, number, number][] = []) => P('chart', id, name, short, bias, section, 0, about, { path, guides });

export const PATTERNS: PatternDef[] = [
  // -- one candle
  CD('doji', 'Doji', 'DOJI', 'neutral', 'Single candle', 1, 'Open and close almost equal: buyers and sellers cancelled out. Indecision; it matters after a long move.', [K(5, 5.1, 8, 2)]),
  CD('dragonfly', 'Dragonfly doji', 'DRG', 'bull', 'Single candle', 1, 'A doji with a long lower wick and no upper wick: price was pushed down and bought back to the open.', [K(8, 8.1, 8.3, 2)]),
  CD('gravestone', 'Gravestone doji', 'GRV', 'bear', 'Single candle', 1, 'A doji with a long upper wick and no lower wick: price was pushed up and sold back to the open.', [K(2.2, 2.1, 8, 1.9)]),
  CD('longleg', 'Long-legged doji', 'LLD', 'neutral', 'Single candle', 1, 'A doji with long wicks on both sides: a wide swing that ended where it began. Strong indecision.', [K(5, 5.1, 9, 1)]),
  CD('hammer', 'Hammer', 'HAM', 'bull', 'Single candle', 1, 'Small body at the top, lower wick at least twice the body, after a fall: sellers were rejected.', [K(7, 7.8, 8, 2)]),
  CD('hangman', 'Hanging man', 'HNG', 'bear', 'Single candle', 1, 'The hammer shape after a rise: a warning that buyers are losing control.', [K(7, 7.8, 8, 2)]),
  CD('invhammer', 'Inverted hammer', 'IHM', 'bull', 'Single candle', 1, 'Small body at the bottom, upper wick at least twice the body, after a fall: buyers tried to turn it.', [K(2.5, 3.2, 8.5, 2.3)]),
  CD('shootstar', 'Shooting star', 'SHS', 'bear', 'Single candle', 1, 'Small body at the bottom, long upper wick, after a rise: buyers were rejected at the top.', [K(3.2, 2.5, 8.5, 2.3)]),
  CD('bullmaru', 'Bullish marubozu', 'MBZ+', 'bull', 'Single candle', 1, 'A green candle that is almost all body: buyers ran the whole candle.', [K(2, 8, 8.2, 1.8)]),
  CD('bearmaru', 'Bearish marubozu', 'MBZ-', 'bear', 'Single candle', 1, 'A red candle that is almost all body: sellers ran the whole candle.', [K(8, 2, 8.2, 1.8)]),
  CD('spinning', 'Spinning top', 'SPN', 'neutral', 'Single candle', 1, 'A small body with wicks on both sides: neither side won the candle.', [K(4.6, 5.6, 8, 2)]),
  CD('biggreen', 'Big green candle', 'BIG+', 'bull', 'Single candle', 1, 'A green body much bigger than the recent candles: a burst of buying.', [K(2.5, 7.8, 8.2, 2.2)]),
  CD('bigred', 'Big red candle', 'BIG-', 'bear', 'Single candle', 1, 'A red body much bigger than the recent candles: a burst of selling.', [K(7.8, 2.5, 8.2, 2.2)]),
  // -- two candles
  CD('bullengulf', 'Bullish engulfing', 'ENG+', 'bull', 'Two candles', 2, 'A green candle whose body swallows the red body before it, after a fall.', [K(6.5, 4.5, 7, 4), K(4, 8, 8.4, 3.6)]),
  CD('bearengulf', 'Bearish engulfing', 'ENG-', 'bear', 'Two candles', 2, 'A red candle whose body swallows the green body before it, after a rise.', [K(4.5, 6.5, 7, 4), K(7, 3, 7.4, 2.6)]),
  CD('bullharami', 'Bullish harami', 'HAR+', 'bull', 'Two candles', 2, 'A small green candle inside the body of a big red one, after a fall: the fall is slowing.', [K(8, 3, 8.4, 2.6), K(4.8, 6, 6.4, 4.4)]),
  CD('bearharami', 'Bearish harami', 'HAR-', 'bear', 'Two candles', 2, 'A small red candle inside the body of a big green one, after a rise: the rise is slowing.', [K(3, 8, 8.4, 2.6), K(6.4, 5, 6.8, 4.6)]),
  CD('haramicross', 'Harami cross', 'HCR', 'neutral', 'Two candles', 2, 'A doji inside the body of a big candle: the move stopped dead.', [K(8, 3, 8.4, 2.6), K(5.5, 5.55, 6.6, 4.4)]),
  CD('piercing', 'Piercing line', 'PRC', 'bull', 'Two candles', 2, 'After a big red candle, a green candle opens lower and closes above the red body\'s middle.', [K(8, 3.5, 8.4, 3), K(3, 6.5, 6.9, 2.6)]),
  CD('darkcloud', 'Dark cloud cover', 'DCC', 'bear', 'Two candles', 2, 'After a big green candle, a red candle opens higher and closes below the green body\'s middle.', [K(3.5, 8, 8.4, 3), K(8.5, 5, 8.9, 4.6)]),
  CD('tweezbot', 'Tweezer bottom', 'TWZ+', 'bull', 'Two candles', 2, 'Two candles, red then green, with the same low after a fall: the low held twice.', [K(7, 4, 7.4, 2.5), K(4, 6.5, 6.9, 2.5)]),
  CD('tweeztop', 'Tweezer top', 'TWZ-', 'bear', 'Two candles', 2, 'Two candles, green then red, with the same high after a rise: the high held twice.', [K(4, 7, 7.5, 3.6), K(7, 4.5, 7.5, 4)]),
  CD('insidebar', 'Inside bar', 'INS', 'neutral', 'Two candles', 2, 'A candle whose whole range sits inside the one before: a pause before the next move.', [K(3, 8, 8.5, 2.5), K(5, 6, 7, 4)]),
  CD('outsidebar', 'Outside bar', 'OUT', 'neutral', 'Two candles', 2, 'A candle whose range covers the whole of the one before: a wide swing that took both sides.', [K(5, 6, 6.6, 4.4), K(4.4, 7.4, 8.6, 3.4)]),
  // -- three candles
  CD('morningstar', 'Morning star', 'MST', 'bull', 'Three candles', 3, 'A big red candle, a small candle, then a green one closing past the middle of the first: a fall turning up.', [K(8, 3.5, 8.4, 3), K(3, 2.4, 3.4, 2), K(2.8, 7, 7.4, 2.4)]),
  CD('morningdoji', 'Morning doji star', 'MDS', 'bull', 'Three candles', 3, 'The morning star with a doji in the middle: stronger indecision before the turn up.', [K(8, 3.5, 8.4, 3), K(2.8, 2.85, 3.4, 2.2), K(2.8, 7, 7.4, 2.4)]),
  CD('eveningstar', 'Evening star', 'EST', 'bear', 'Three candles', 3, 'A big green candle, a small candle, then a red one closing past the middle of the first: a rise turning down.', [K(3, 7.5, 8, 2.6), K(8, 8.6, 9, 7.6), K(8.2, 4, 8.6, 3.6)]),
  CD('eveningdoji', 'Evening doji star', 'EDS', 'bear', 'Three candles', 3, 'The evening star with a doji in the middle.', [K(3, 7.5, 8, 2.6), K(8.2, 8.25, 9, 7.6), K(8.2, 4, 8.6, 3.6)]),
  CD('soldiers', 'Three white soldiers', '3WS', 'bull', 'Three candles', 3, 'Three strong green candles in a row, each closing higher: steady buying.', [K(2, 4.4, 4.7, 1.8), K(4, 6.4, 6.7, 3.8), K(6, 8.6, 8.9, 5.8)]),
  CD('crows', 'Three black crows', '3BC', 'bear', 'Three candles', 3, 'Three strong red candles in a row, each closing lower: steady selling.', [K(8.6, 6.2, 8.9, 5.9), K(6.6, 4.2, 6.9, 3.9), K(4.6, 2, 4.9, 1.7)]),
  CD('threeinup', 'Three inside up', '3IU', 'bull', 'Three candles', 3, 'A bullish harami confirmed by a third green candle closing above the first one\'s open.', [K(8, 3, 8.4, 2.6), K(4.5, 6, 6.4, 4.2), K(6, 8.8, 9.1, 5.8)]),
  CD('threeindown', 'Three inside down', '3ID', 'bear', 'Three candles', 3, 'A bearish harami confirmed by a third red candle closing below the first one\'s open.', [K(3, 8, 8.4, 2.6), K(6.4, 5, 6.8, 4.6), K(5, 2.2, 5.3, 1.9)]),
  CD('threeoutup', 'Three outside up', '3OU', 'bull', 'Three candles', 3, 'A bullish engulfing followed by another green candle closing higher.', [K(6, 4.5, 6.4, 4.2), K(4.2, 7.2, 7.6, 3.9), K(7, 9, 9.3, 6.8)]),
  CD('threeoutdown', 'Three outside down', '3OD', 'bear', 'Three candles', 3, 'A bearish engulfing followed by another red candle closing lower.', [K(4.5, 6, 6.4, 4.2), K(6.4, 3.4, 6.8, 3.1), K(3.6, 1.6, 3.9, 1.3)]),
  // -- five candles
  CD('rising3', 'Rising three methods', 'R3M', 'bull', 'Five candles', 5, 'A big green candle, three small pullback candles inside its range, then another green candle closing above it: the uptrend goes on.', [K(2, 7, 7.3, 1.8), K(6.5, 5.8, 6.9, 5.5), K(5.8, 5.2, 6.1, 4.9), K(5.2, 4.6, 5.5, 4.3), K(4.7, 8.4, 8.7, 4.4)]),
  CD('falling3', 'Falling three methods', 'F3M', 'bear', 'Five candles', 5, 'A big red candle, three small bounce candles inside its range, then another red candle closing below it: the downtrend goes on.', [K(8, 3, 8.2, 2.7), K(3.5, 4.2, 4.5, 3.1), K(4.2, 4.8, 5.1, 3.9), K(4.8, 5.4, 5.7, 4.5), K(5.3, 1.6, 5.6, 1.3)]),
  // -- chart patterns
  CH('dtop', 'Double top', '2T', 'bear', 'Reversal', 'Price hits the same high twice and falls back between them: buyers failed twice at one level.', [[0, 2], [2, 8], [4, 4], [6, 8], [8, 3]], [[0, 4, 8, 4]]),
  CH('dbottom', 'Double bottom', '2B', 'bull', 'Reversal', 'Price drops to the same low twice and bounces between them: sellers failed twice at one level.', [[0, 8], [2, 2], [4, 6], [6, 2], [8, 7]], [[0, 6, 8, 6]]),
  CH('ttop', 'Triple top', '3T', 'bear', 'Reversal', 'Three highs at the same level: a ceiling that held three times.', [[0, 2], [1.3, 8], [2.6, 4], [4, 8], [5.3, 4], [6.6, 8], [8, 2]], [[0, 4, 8, 4]]),
  CH('tbottom', 'Triple bottom', '3B', 'bull', 'Reversal', 'Three lows at the same level: a floor that held three times.', [[0, 8], [1.3, 2], [2.6, 6], [4, 2], [5.3, 6], [6.6, 2], [8, 8]], [[0, 6, 8, 6]]),
  CH('hns', 'Head and shoulders', 'H&S', 'bear', 'Reversal', 'Three highs, the middle one the highest, with two lows at about the same level (the neckline): a rise running out of steam.', [[0, 2], [1.5, 6], [2.5, 4], [4, 9], [5.5, 4], [6.5, 6], [8, 2]], [[2.5, 4, 5.5, 4]]),
  CH('ihns', 'Inverse head and shoulders', 'iH&S', 'bull', 'Reversal', 'Three lows, the middle one the lowest, with two highs at about the same level: a fall running out of steam.', [[0, 8], [1.5, 4], [2.5, 6], [4, 1], [5.5, 6], [6.5, 4], [8, 8]], [[2.5, 6, 5.5, 6]]),
  CH('asctri', 'Ascending triangle', 'ATR', 'bull', 'Continuation', 'A flat top and rising lows squeezing into it: buyers keep pushing at one ceiling.', [[0, 2], [1, 8], [2.5, 4], [4, 8], [5.5, 5.5], [7, 8]], [[1, 8, 7, 8], [0, 2, 7, 7]]),
  CH('desctri', 'Descending triangle', 'DTR', 'bear', 'Continuation', 'A flat bottom and falling highs squeezing into it: sellers keep pushing at one floor.', [[0, 8], [1, 2], [2.5, 6], [4, 2], [5.5, 4.5], [7, 2]], [[1, 2, 7, 2], [0, 8, 7, 3]]),
  CH('symtri', 'Symmetrical triangle', 'STR', 'neutral', 'Range / indecision', 'Lower highs and higher lows closing in on each other: the market is coiling before it breaks out.', [[0, 5], [1, 9], [2.5, 2], [4, 7.5], [5.5, 3.5], [7, 6]], [[1, 9, 7, 6], [2.5, 2, 7, 4]]),
  CH('risewedge', 'Rising wedge', 'RWG', 'bear', 'Reversal', 'Highs and lows both rising but the lows rise faster, so the range narrows: a weakening rise, often broken downward.', [[0, 1], [1, 4], [2, 2.5], [3.5, 6], [4.5, 4.5], [6, 7.5]], [[1, 4, 6, 7.5], [0, 1, 4.5, 4.5]]),
  CH('fallwedge', 'Falling wedge', 'FWG', 'bull', 'Reversal', 'Highs and lows both falling but the highs fall faster, so the range narrows: a weakening fall, often broken upward.', [[0, 9], [1, 6], [2, 7.5], [3.5, 4], [4.5, 5.5], [6, 2.5]], [[1, 6, 6, 2.5], [0, 9, 4.5, 5.5]]),
  CH('rect', 'Rectangle (range)', 'RNG', 'neutral', 'Range / indecision', 'Price bounces between a flat ceiling and a flat floor: a sideways range.', [[0, 5], [1, 8], [2.5, 2], [4, 8], [5.5, 2], [7, 8]], [[1, 8, 7, 8], [2.5, 2, 5.5, 2]]),
  CH('upchannel', 'Rising channel', 'CH+', 'neutral', 'Continuation', 'Higher highs and higher lows moving in parallel: an orderly uptrend.', [[0, 1], [1.5, 4], [2.5, 2.5], [4, 5.5], [5, 4], [6.5, 7]], [[1.5, 4, 6.5, 7], [0, 1, 5, 4]]),
  CH('downchannel', 'Falling channel', 'CH-', 'neutral', 'Continuation', 'Lower highs and lower lows moving in parallel: an orderly downtrend.', [[0, 9], [1.5, 6], [2.5, 7.5], [4, 4.5], [5, 6], [6.5, 3]], [[0, 9, 4, 4.5], [2.5, 7.5, 6.5, 3]]),
  CH('broaden', 'Broadening (megaphone)', 'BRD', 'neutral', 'Range / indecision', 'Higher highs and lower lows: the swings keep getting wider and the market is unstable.', [[0, 5], [1, 6.5], [2.5, 3.5], [4, 8], [5.5, 2], [7, 9.5]], [[1, 6.5, 7, 9.5], [2.5, 3.5, 5.5, 2]]),
  CH('bullflag', 'Bull flag', 'FLG+', 'bull', 'Continuation', 'A sharp rise (the pole) followed by a small, gentle pullback: the rise usually resumes.', [[0, 1], [3, 8.5], [4, 7], [5, 7.8], [6, 6.2], [7, 7]], [[3, 8.5, 7, 7], [4, 7, 7, 5.6]]),
  CH('bearflag', 'Bear flag', 'FLG-', 'bear', 'Continuation', 'A sharp fall (the pole) followed by a small, gentle bounce: the fall usually resumes.', [[0, 9], [3, 1.5], [4, 3], [5, 2.2], [6, 3.8], [7, 3]], [[3, 1.5, 7, 3], [4, 3, 7, 4.4]]),
];
export const patternDef = (id: string): PatternDef | undefined => PATTERNS.find((p) => p.id === id);
export const SECTIONS: Record<Group, string[]> = { candle: ['Single candle', 'Two candles', 'Three candles', 'Five candles'], chart: ['Reversal', 'Continuation', 'Range / indecision'] };

// ------------------------------------------------------------------ candle helpers
interface Ctx { ab: number[]; ar: number[] }
const body = (c: PCandle) => Math.abs(c.c - c.o);
const rng = (c: PCandle) => c.h - c.l;
const top = (c: PCandle) => Math.max(c.o, c.c);
const bot = (c: PCandle) => Math.min(c.o, c.c);
const up = (c: PCandle) => c.c > c.o;
const dn = (c: PCandle) => c.c < c.o;
const mid = (c: PCandle) => (c.o + c.c) / 2;
const upper = (c: PCandle) => c.h - top(c);
const lower = (c: PCandle) => bot(c) - c.l;

/** the average body and range of the 14 candles BEFORE each candle (so a candle is judged against what was normal just before it) */
function context(cs: PCandle[]): Ctx {
  const ab: number[] = [], ar: number[] = [];
  let sb = 0, sr = 0;
  for (let i = 0; i < cs.length; i++) {
    const from = Math.max(0, i - 14), n = i - from;
    ab.push(n ? sb / n : 0); ar.push(n ? sr / n : 0);
    sb += body(cs[i]); sr += rng(cs[i]);
    if (i >= 14) { sb -= body(cs[i - 14]); sr -= rng(cs[i - 14]); }
  }
  return { ab, ar };
}

/** how far the five candles before `s` moved, in average candle ranges: > 1 = rose, < -1 = fell */
const lead = (cs: PCandle[], s: number, x: Ctx): number => (s >= 6 && x.ar[s] > 0 ? (cs[s - 1].c - cs[s - 6].c) / x.ar[s] : 0);
// A bullish reversal needs the move before it not to have been a clear rise; a bearish one not a clear fall (a flat lead is allowed: a hammer shape is still a hammer, and
// the market structure shown with it says how much it is worth). The hammer family is split by direction instead: after a rise it is a hanging man / shooting star.
const wasDown = (cs: PCandle[], s: number, x: Ctx) => lead(cs, s, x) <= 0.6;
const wasUp = (cs: PCandle[], s: number, x: Ctx) => lead(cs, s, x) >= -0.6;
const rising = (cs: PCandle[], s: number, x: Ctx) => lead(cs, s, x) > 0;

type Test = (cs: PCandle[], i: number, x: Ctx) => boolean;
const isDoji = (c: PCandle) => rng(c) > 0 && body(c) <= 0.1 * rng(c);
const big = (c: PCandle, i: number, x: Ctx) => body(c) >= x.ab[i] && body(c) >= 0.5 * rng(c);

const hammerShape = (c: PCandle) => rng(c) > 0 && body(c) <= 0.4 * rng(c) && lower(c) >= 2 * Math.max(body(c), rng(c) * 0.05) && upper(c) <= 0.3 * rng(c) && lower(c) >= 0.5 * rng(c);
const starShape = (c: PCandle) => rng(c) > 0 && body(c) <= 0.4 * rng(c) && upper(c) >= 2 * Math.max(body(c), rng(c) * 0.05) && lower(c) <= 0.3 * rng(c) && upper(c) >= 0.5 * rng(c);
const meaningful = (c: PCandle, i: number, x: Ctx) => rng(c) >= 0.5 * x.ar[i];            // a candle must not be tiny against the recent ones

const CANDLE_TESTS: Record<string, Test> = {
  doji: (cs, i, x) => isDoji(cs[i]) && meaningful(cs[i], i, x),
  dragonfly: (cs, i, x) => isDoji(cs[i]) && upper(cs[i]) <= 0.1 * rng(cs[i]) && lower(cs[i]) >= 0.6 * rng(cs[i]) && meaningful(cs[i], i, x),
  gravestone: (cs, i, x) => isDoji(cs[i]) && lower(cs[i]) <= 0.1 * rng(cs[i]) && upper(cs[i]) >= 0.6 * rng(cs[i]) && meaningful(cs[i], i, x),
  longleg: (cs, i, x) => isDoji(cs[i]) && upper(cs[i]) >= 0.3 * rng(cs[i]) && lower(cs[i]) >= 0.3 * rng(cs[i]) && rng(cs[i]) >= 1.2 * x.ar[i],
  hammer: (cs, i, x) => hammerShape(cs[i]) && meaningful(cs[i], i, x) && !rising(cs, i, x),
  hangman: (cs, i, x) => hammerShape(cs[i]) && meaningful(cs[i], i, x) && rising(cs, i, x),
  invhammer: (cs, i, x) => starShape(cs[i]) && meaningful(cs[i], i, x) && !rising(cs, i, x),
  shootstar: (cs, i, x) => starShape(cs[i]) && meaningful(cs[i], i, x) && rising(cs, i, x),
  bullmaru: (cs, i, x) => up(cs[i]) && body(cs[i]) >= 0.9 * rng(cs[i]) && rng(cs[i]) >= 0.8 * x.ar[i],
  bearmaru: (cs, i, x) => dn(cs[i]) && body(cs[i]) >= 0.9 * rng(cs[i]) && rng(cs[i]) >= 0.8 * x.ar[i],
  spinning: (cs, i, x) => rng(cs[i]) > 0 && body(cs[i]) > 0.1 * rng(cs[i]) && body(cs[i]) <= 0.3 * rng(cs[i]) && upper(cs[i]) >= 0.25 * rng(cs[i]) && lower(cs[i]) >= 0.25 * rng(cs[i]) && meaningful(cs[i], i, x),
  biggreen: (cs, i, x) => up(cs[i]) && x.ab[i] > 0 && body(cs[i]) >= 1.8 * x.ab[i] && body(cs[i]) >= 0.6 * rng(cs[i]),
  bigred: (cs, i, x) => dn(cs[i]) && x.ab[i] > 0 && body(cs[i]) >= 1.8 * x.ab[i] && body(cs[i]) >= 0.6 * rng(cs[i]),

  bullengulf: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return dn(p) && up(q) && q.o <= p.c && q.c >= p.o && body(q) > body(p) && wasDown(cs, i - 1, x); },
  bearengulf: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return up(p) && dn(q) && q.o >= p.c && q.c <= p.o && body(q) > body(p) && wasUp(cs, i - 1, x); },
  bullharami: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return dn(p) && big(p, i - 1, x) && up(q) && top(q) < top(p) && bot(q) > bot(p) && body(q) <= 0.6 * body(p) && wasDown(cs, i - 1, x); },
  bearharami: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return up(p) && big(p, i - 1, x) && dn(q) && top(q) < top(p) && bot(q) > bot(p) && body(q) <= 0.6 * body(p) && wasUp(cs, i - 1, x); },
  haramicross: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return big(p, i - 1, x) && isDoji(q) && top(q) < top(p) && bot(q) > bot(p); },
  piercing: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return dn(p) && big(p, i - 1, x) && up(q) && q.o <= p.c && q.c > mid(p) && q.c < p.o && wasDown(cs, i - 1, x); },
  darkcloud: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return up(p) && big(p, i - 1, x) && dn(q) && q.o >= p.c && q.c < mid(p) && q.c > p.o && wasUp(cs, i - 1, x); },
  tweezbot: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return dn(p) && up(q) && Math.abs(p.l - q.l) <= 0.05 * x.ar[i] + 1e-12 && p.l > 0 && rng(p) >= 0.5 * x.ar[i] && wasDown(cs, i - 1, x); },
  tweeztop: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return up(p) && dn(q) && Math.abs(p.h - q.h) <= 0.05 * x.ar[i] + 1e-12 && rng(p) >= 0.5 * x.ar[i] && wasUp(cs, i - 1, x); },
  insidebar: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return q.h < p.h && q.l > p.l && rng(p) >= 0.8 * x.ar[i]; },
  outsidebar: (cs, i, x) => { const p = cs[i - 1], q = cs[i]; return q.h > p.h && q.l < p.l && rng(q) >= 1.2 * x.ar[i]; },

  morningstar: (cs, i, x) => { const a = cs[i - 2], b = cs[i - 1], c = cs[i]; return dn(a) && big(a, i - 2, x) && body(b) <= 0.4 * body(a) && !isDoji(b) && up(c) && c.c > mid(a) && wasDown(cs, i - 2, x); },
  morningdoji: (cs, i, x) => { const a = cs[i - 2], b = cs[i - 1], c = cs[i]; return dn(a) && big(a, i - 2, x) && isDoji(b) && up(c) && c.c > mid(a) && wasDown(cs, i - 2, x); },
  eveningstar: (cs, i, x) => { const a = cs[i - 2], b = cs[i - 1], c = cs[i]; return up(a) && big(a, i - 2, x) && body(b) <= 0.4 * body(a) && !isDoji(b) && dn(c) && c.c < mid(a) && wasUp(cs, i - 2, x); },
  eveningdoji: (cs, i, x) => { const a = cs[i - 2], b = cs[i - 1], c = cs[i]; return up(a) && big(a, i - 2, x) && isDoji(b) && dn(c) && c.c < mid(a) && wasUp(cs, i - 2, x); },
  soldiers: (cs, i, x) => { const t = [cs[i - 2], cs[i - 1], cs[i]]; return t.every((c, k) => up(c) && body(c) >= 0.6 * rng(c) && body(c) >= 0.8 * x.ab[i] && upper(c) <= 0.35 * body(c) && (k === 0 || (c.c > t[k - 1].c && c.o > t[k - 1].o && c.o <= t[k - 1].c))); },
  crows: (cs, i, x) => { const t = [cs[i - 2], cs[i - 1], cs[i]]; return t.every((c, k) => dn(c) && body(c) >= 0.6 * rng(c) && body(c) >= 0.8 * x.ab[i] && lower(c) <= 0.35 * body(c) && (k === 0 || (c.c < t[k - 1].c && c.o < t[k - 1].o && c.o >= t[k - 1].c))); },
  threeinup: (cs, i, x) => CANDLE_TESTS.bullharami(cs, i - 1, x) && up(cs[i]) && cs[i].c > cs[i - 2].o,
  threeindown: (cs, i, x) => CANDLE_TESTS.bearharami(cs, i - 1, x) && dn(cs[i]) && cs[i].c < cs[i - 2].o,
  threeoutup: (cs, i, x) => CANDLE_TESTS.bullengulf(cs, i - 1, x) && up(cs[i]) && cs[i].c > cs[i - 1].c,
  threeoutdown: (cs, i, x) => CANDLE_TESTS.bearengulf(cs, i - 1, x) && dn(cs[i]) && cs[i].c < cs[i - 1].c,

  rising3: (cs, i, x) => {
    const a = cs[i - 4], m = [cs[i - 3], cs[i - 2], cs[i - 1]], e = cs[i];
    return up(a) && big(a, i - 4, x) && m.every((c) => c.h <= a.h && c.l >= a.l && body(c) < body(a) * 0.7) && m[2].c <= m[0].c && up(e) && e.c > a.c && body(e) >= x.ab[i];
  },
  falling3: (cs, i, x) => {
    const a = cs[i - 4], m = [cs[i - 3], cs[i - 2], cs[i - 1]], e = cs[i];
    return dn(a) && big(a, i - 4, x) && m.every((c) => c.h <= a.h && c.l >= a.l && body(c) < body(a) * 0.7) && m[2].c >= m[0].c && dn(e) && e.c < a.c && body(e) >= x.ab[i];
  },
};

// ------------------------------------------------------------------ chart patterns: swing highs / lows
interface Pivot { i: number; p: number; hi: boolean; at: number }          // at = the candle that confirms it (k candles later)
const PIVOT_K = 3;

/** Alternating swing highs and lows: a high is the highest of the K candles on each side, a low the lowest; a run of two highs in a row keeps the higher one. */
export function pivots(cs: PCandle[], k = PIVOT_K): Pivot[] {
  const out: Pivot[] = [];
  for (let j = k; j < cs.length - k; j++) {
    let isH = true, isL = true;
    for (let d = 1; d <= k && (isH || isL); d++) {
      if (!(cs[j].h > cs[j - d].h && cs[j].h >= cs[j + d].h)) isH = false;
      if (!(cs[j].l < cs[j - d].l && cs[j].l <= cs[j + d].l)) isL = false;
    }
    if (isH === isL) continue;
    const pv: Pivot = { i: j, p: isH ? cs[j].h : cs[j].l, hi: isH, at: j + k };
    const last = out[out.length - 1];
    if (last && last.hi === pv.hi) { if (pv.hi ? pv.p > last.p : pv.p < last.p) out[out.length - 1] = pv; }
    else out.push(pv);
  }
  return out;
}

const MIN_SPAN = 14, MAX_SPAN = 140;
const highs = (v: Pivot[]) => v.filter((p) => p.hi), lows = (v: Pivot[]) => v.filter((p) => !p.hi);
const spread = (v: Pivot[]) => (v.length ? Math.max(...v.map((p) => p.p)) - Math.min(...v.map((p) => p.p)) : 0);
const flat = (v: Pivot[], tol: number) => v.length >= 2 && spread(v) <= tol;
const slope = (a: Pivot, b: Pivot) => (b.p - a.p) / Math.max(1, b.i - a.i);
const lineAt = (a: Pivot, b: Pivot, i: number) => a.p + slope(a, b) * (i - a.i);
const rises = (v: Pivot[], tol: number) => v.length >= 2 && v.every((p, k) => k === 0 || p.p > v[k - 1].p + tol * 0.3) && v[v.length - 1].p - v[0].p > tol;
const falls = (v: Pivot[], tol: number) => v.length >= 2 && v.every((p, k) => k === 0 || p.p < v[k - 1].p - tol * 0.3) && v[0].p - v[v.length - 1].p > tol;
const pair = (v: Pivot[]): [Pivot, Pivot] => [v[0], v[v.length - 1]];

/** for each chart pattern: how many swing points it needs (any of these counts) and the test on them. `tol` = how far two "equal" levels may differ. */
const CHART: { id: string; ms: number[]; test: (v: Pivot[], tol: number, ar: number) => boolean }[] = [];
const defChart = (id: string, ms: number[], test: (v: Pivot[], tol: number, ar: number) => boolean) => CHART.push({ id, ms, test });

const deep = (peaks: Pivot[], troughs: Pivot[], tol: number) => Math.min(...peaks.map((p) => p.p)) - Math.max(...troughs.map((p) => p.p)) >= 2.5 * tol;
const deepDown = (troughs: Pivot[], peaks: Pivot[], tol: number) => Math.min(...peaks.map((p) => p.p)) - Math.max(...troughs.map((p) => p.p)) >= 2.5 * tol;

defChart('dtop', [3], (v, tol) => v[0].hi && flat([v[0], v[2]], tol) && v[0].p - v[1].p >= 2.5 * tol);
defChart('dbottom', [3], (v, tol) => !v[0].hi && flat([v[0], v[2]], tol) && v[1].p - v[0].p >= 2.5 * tol);
defChart('ttop', [5], (v, tol) => v[0].hi && flat([v[0], v[2], v[4]], tol) && deep([v[0], v[2], v[4]], [v[1], v[3]], tol));
defChart('tbottom', [5], (v, tol) => !v[0].hi && flat([v[0], v[2], v[4]], tol) && deepDown([v[0], v[2], v[4]], [v[1], v[3]], tol));
defChart('hns', [5], (v, tol) => {
  if (!v[0].hi) return false;
  const [s1, n1, h, n2, s2] = v, h0 = h.p - Math.max(s1.p, s2.p);
  return h0 >= 2 * tol && Math.abs(s1.p - s2.p) <= Math.max(tol, 0.4 * (h.p - Math.max(n1.p, n2.p))) && Math.abs(n1.p - n2.p) <= 1.5 * tol && Math.min(s1.p, s2.p) - Math.max(n1.p, n2.p) >= tol;
});
defChart('ihns', [5], (v, tol) => {
  if (v[0].hi) return false;
  const [s1, n1, h, n2, s2] = v, h0 = Math.min(s1.p, s2.p) - h.p;
  return h0 >= 2 * tol && Math.abs(s1.p - s2.p) <= Math.max(tol, 0.4 * (Math.min(n1.p, n2.p) - h.p)) && Math.abs(n1.p - n2.p) <= 1.5 * tol && Math.min(n1.p, n2.p) - Math.max(s1.p, s2.p) >= tol;
});
const hl = (v: Pivot[]): [Pivot[], Pivot[]] => [highs(v), lows(v)];
const gap = (H: Pivot[], L: Pivot[]) => { const [h0, h1] = pair(H), [l0, l1] = pair(L); const a = lineAt(h0, h1, h0.i) - lineAt(l0, l1, h0.i), b = lineAt(h0, h1, h1.i) - lineAt(l0, l1, h1.i); return [a, b] as const; };
defChart('asctri', [4, 5, 6], (v, tol) => { const [H, L] = hl(v); return H.length >= 2 && L.length >= 2 && flat(H, tol) && rises(L, tol) && gap(H, L)[1] > 0; });
defChart('desctri', [4, 5, 6], (v, tol) => { const [H, L] = hl(v); return H.length >= 2 && L.length >= 2 && flat(L, tol) && falls(H, tol) && gap(H, L)[1] > 0; });
defChart('symtri', [4, 5, 6], (v, tol) => { const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !falls(H, tol) || !rises(L, tol)) return false; const [a, b] = gap(H, L); return b > 0 && b < 0.7 * a; });
defChart('risewedge', [5, 6], (v, tol) => {
  const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !rises(H, tol) || !rises(L, tol)) return false;
  const [a, b] = gap(H, L); return b > 0 && b < 0.75 * a;
});
defChart('fallwedge', [5, 6], (v, tol) => {
  const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !falls(H, tol) || !falls(L, tol)) return false;
  const [a, b] = gap(H, L); return b > 0 && b < 0.75 * a;
});
defChart('rect', [5, 6], (v, tol) => { const [H, L] = hl(v); return H.length >= 2 && L.length >= 2 && flat(H, tol) && flat(L, tol) && Math.min(...H.map((p) => p.p)) - Math.max(...L.map((p) => p.p)) >= 3 * tol; });
defChart('upchannel', [5, 6], (v, tol) => {
  const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !rises(H, tol) || !rises(L, tol)) return false;
  const [a, b] = gap(H, L); return a > 0 && b > 0 && b >= 0.75 * a && b <= 1.3 * a;
});
defChart('downchannel', [5, 6], (v, tol) => {
  const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !falls(H, tol) || !falls(L, tol)) return false;
  const [a, b] = gap(H, L); return a > 0 && b > 0 && b >= 0.75 * a && b <= 1.3 * a;
});
defChart('broaden', [5, 6], (v, tol) => {
  const [H, L] = hl(v); if (H.length < 2 || L.length < 2 || !rises(H, tol) || !falls(L, tol)) return false;
  const [a, b] = gap(H, L); return a > 0 && b > 1.3 * a;
});
// a flag: a pole (one big swing) then a small drifting pullback of at least 2 swings that gives back no more than half of it
defChart('bullflag', [4, 5], (v, tol, ar) => {
  if (v[0].hi) return false;
  const pole = v[1].p - v[0].p, rest = v.slice(2), back = v[1].p - Math.min(...rest.filter((p) => !p.hi).map((p) => p.p));
  const rh = rest.filter((p) => p.hi), rl = rest.filter((p) => !p.hi);
  return pole >= 4 * ar && v[1].i - v[0].i <= 12 && rl.length >= 1 && rh.length >= 1 && back > 0 && back <= 0.5 * pole && rh.every((p) => p.p <= v[1].p) && (rh.length < 2 || rh[rh.length - 1].p < rh[0].p + tol * 0.3) && rest[rest.length - 1].i - v[1].i >= 4;
});
defChart('bearflag', [4, 5], (v, tol, ar) => {
  if (!v[0].hi) return false;
  const pole = v[0].p - v[1].p, rest = v.slice(2), back = Math.max(...rest.filter((p) => p.hi).map((p) => p.p)) - v[1].p;
  const rh = rest.filter((p) => p.hi), rl = rest.filter((p) => !p.hi);
  return pole >= 4 * ar && v[1].i - v[0].i <= 12 && rl.length >= 1 && rh.length >= 1 && back > 0 && back <= 0.5 * pole && rl.every((p) => p.p >= v[1].p) && (rl.length < 2 || rl[rl.length - 1].p > rl[0].p - tol * 0.3) && rest[rest.length - 1].i - v[1].i >= 4;
});

/** chart patterns that reverse a move: the direction price must have come from (the swing before the first point is at least as big as the pattern itself) */
const APPROACH: Record<string, 'up' | 'down'> = { dtop: 'up', ttop: 'up', hns: 'up', dbottom: 'down', tbottom: 'down', ihns: 'down' };

/** the lines that draw a chart pattern */
function drawLines(id: string, v: Pivot[]): [number, number, number, number, boolean?][] {
  const [H, L] = hl(v);
  const seg = (a: Pivot, b: Pivot, d = false): [number, number, number, number, boolean?] => [a.i, a.p, b.i, b.p, d];
  const first = v[0], last = v[v.length - 1];
  const zig = v.slice(1).map((p, k) => seg(v[k], p, true));
  switch (id) {
    case 'dtop': case 'dbottom': return [seg(v[0], v[2]), ...zig];
    case 'ttop': case 'tbottom': return [seg(v[0], v[4]), ...zig];
    case 'hns': case 'ihns': return [seg(v[1], v[3]), ...zig];
    case 'asctri': case 'desctri': case 'symtri': case 'risewedge': case 'fallwedge': case 'upchannel': case 'downchannel': case 'broaden': case 'rect':
      return [seg(...pair(H)), seg(...pair(L)), ...zig];
    case 'bullflag': case 'bearflag': return [seg(first, v[1]), ...(H.length > 1 ? [seg(H[H.length > 2 ? 1 : 0], H[H.length - 1])] : []), ...zig.slice(1)];
    default: return [seg(first, last)];
  }
}

// ------------------------------------------------------------------ market structure
/** The market structure just BEFORE candle `s`, from the swing points already confirmed then: higher highs and higher lows = uptrend, lower highs and lower lows = downtrend,
 *  anything else (or too little history) = sideways. Differences smaller than a fifth of the average candle range count as equal. */
export function structureAt(pv: Pivot[], s: number, ar: number): Structure {
  let n = 0;
  while (n < pv.length && pv[n].at < s) n++;                       // pivots confirmed before s (pv is in time order)
  const h: number[] = [], l: number[] = [];
  for (let k = n - 1; k >= 0 && (h.length < 2 || l.length < 2); k--) { const t = pv[k].hi ? h : l; if (t.length < 2) t.push(pv[k].p); }
  if (h.length < 2 || l.length < 2) return 'side';
  const tol = 0.2 * ar;                                             // h[0] / l[0] are the newest swings
  if (h[0] > h[1] + tol && l[0] > l[1] + tol) return 'up';
  if (h[0] < h[1] - tol && l[0] < l[1] - tol) return 'down';
  return 'side';
}

// ------------------------------------------------------------------ the finder
/** Every wanted pattern found on the candles. `ids` = the patterns to look for (default: all). Candles that were filled in (`syn`) never take part in a pattern. */
export function detect(cs: PCandle[], ids?: ReadonlySet<string> | string[]): Detection[] {
  const want = ids ? new Set(ids) : null;
  const out: Detection[] = [];
  if (cs.length < 3) return out;
  const x = context(cs);
  const pv = pivots(cs);
  const defs = new Map(PATTERNS.map((p) => [p.id, p]));
  const any = (s: number, e: number) => { for (let i = s; i <= e; i++) if (cs[i].syn) return true; return false; };

  for (const def of PATTERNS) {
    if (def.group !== 'candle' || (want && !want.has(def.id))) continue;
    const test = CANDLE_TESTS[def.id];
    if (!test) continue;
    for (let i = Math.max(def.size - 1, 15); i < cs.length; i++) {
      const s = i - def.size + 1;
      if (any(s, i) || x.ar[i] <= 0 || x.ab[i] < 0) continue;
      let ok = false;
      try { ok = test(cs, i, x); } catch { ok = false; }
      if (!ok) continue;
      let lo = Infinity, hi = -Infinity;
      for (let j = s; j <= i; j++) { lo = Math.min(lo, cs[j].l); hi = Math.max(hi, cs[j].h); }
      let bias = def.bias;
      if (def.id === 'haramicross') bias = dn(cs[i - 1]) ? 'bull' : 'bear';
      out.push({ id: def.id, bias, structure: structureAt(pv, s, x.ar[s] || x.ar[i]), i0: s, i1: i, at: i, t0: cs[s].t, t1: cs[i].t, tAt: cs[i].t, lo, hi, lines: [] });
    }
  }

  const chartIds = CHART.filter((c) => !want || want.has(c.id));
  if (chartIds.length) {
    const lastEnd = new Map<string, number>();
    for (let e = 0; e < pv.length; e++) {
      for (const c of chartIds) {
        for (const m of c.ms) {
          const s = e - m + 1;
          if (s < 0) continue;
          const v = pv.slice(s, e + 1);
          const span = v[v.length - 1].i - v[0].i;
          if (span < MIN_SPAN || span > MAX_SPAN || any(v[0].i, v[v.length - 1].i)) continue;
          if (v[0].i <= (lastEnd.get(c.id) ?? -1)) continue;                       // the same stretch is not reported twice
          const ar = x.ar[v[v.length - 1].i] || 0;
          if (ar <= 0) continue;
          const height = spread(v);
          if (height < 4 * ar) continue;                                           // a shape smaller than a few candles' range is noise
          const tol = Math.max(0.12 * height, 0.5 * ar);
          let ok = false;
          try { ok = c.test(v, tol, ar); } catch { ok = false; }
          if (!ok) continue;
          const need = APPROACH[c.id];                                         // a reversal shape (M, W, head and shoulders) only counts after a move into it
          if (need) {
            let ext = need === 'up' ? Infinity : -Infinity;
            for (let j = Math.max(0, v[0].i - 25); j <= v[0].i; j++) ext = need === 'up' ? Math.min(ext, cs[j].l) : Math.max(ext, cs[j].h);
            const moved = need === 'up' ? v[0].p - ext : ext - v[0].p;
            if (!(moved >= 0.9 * height)) continue;
          }
          const def = defs.get(c.id)!;
          const lines = drawLines(c.id, v).map(([a, p0, b, p1, d]) => ({ t0: cs[a].t, p0, t1: cs[b].t, p1, dash: !!d }));
          const at = Math.min(cs.length - 1, v[v.length - 1].at);
          out.push({ id: c.id, bias: def.bias, structure: structureAt(pv, v[0].i, ar), i0: v[0].i, i1: v[v.length - 1].i, at, t0: cs[v[0].i].t, t1: cs[v[v.length - 1].i].t, tAt: cs[at].t, lo: Math.min(...v.map((p) => p.p)), hi: Math.max(...v.map((p) => p.p)), lines });
          lastEnd.set(c.id, v[v.length - 1].i);
          break;
        }
      }
    }
  }
  return out.sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
}

/** how many times each pattern was found: the number on a grid card */
export function countFound(ds: Detection[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const d of ds) m.set(d.id, (m.get(d.id) ?? 0) + 1);
  return m;
}

/** the patterns that were completed by candle `at` (an alert is raised for these) */
export const completedAt = (ds: Detection[], t: number): Detection[] => ds.filter((d) => d.tAt === t);
