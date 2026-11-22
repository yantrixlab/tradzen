/** Pure helpers for "When you trade" on My trades: the weekday x hour grid, its totals, the colours and the highlights. The server (backend/app/broker_trades.py `hours`) sends one
 *  entry per slot that has trades; days are 0 = Sunday .. 6 = Saturday, hours 0..23, both on the viewer's own clock. */

export interface HourStat { n: number; wins: number; losses: number; draws: number; win_rate: number | null; staked: number; net: number; roi: number | null }
export interface HourCell extends HourStat { d: number; h: number }
export interface HoursResp { cells: HourCell[]; summary: HourStat }
export type Metric = 'trades' | 'net' | 'rate';

/** A slot, an hour or a day needs at least this many trades before it can be called the best or the worst: one lucky trade says nothing. */
export const MIN_PICK = 3;

const round2 = (x: number) => Math.round(x * 100) / 100;
export const emptyStat = (): HourStat => ({ n: 0, wins: 0, losses: 0, draws: 0, win_rate: null, staked: 0, net: 0, roi: null });

export function addStat(a: HourStat, b: HourStat): HourStat {
  const wins = a.wins + b.wins, losses = a.losses + b.losses, staked = round2(a.staked + b.staked), net = round2(a.net + b.net);
  return { n: a.n + b.n, wins, losses, draws: a.draws + b.draws, win_rate: wins + losses ? wins / (wins + losses) : null, staked, net, roi: staked ? net / staked : null };
}

export interface Matrix {
  rows: (HourStat | null)[][];         // [hour 0..23][column 0..6]; the columns follow the week's first day
  hourTotals: HourStat[];              // per hour, all days together
  dayTotals: HourStat[];               // per column, all hours together
  total: HourStat;
}

/** Lay the slots out as 24 hours x 7 columns. `weekStart` = the day the week starts on (0 = Sunday, 1 = Monday). */
export function buildMatrix(cells: HourCell[], weekStart: 0 | 1): Matrix {
  const rows: (HourStat | null)[][] = Array.from({ length: 24 }, () => Array<HourStat | null>(7).fill(null));
  const hourTotals = Array.from({ length: 24 }, emptyStat), dayTotals = Array.from({ length: 7 }, emptyStat);
  let total = emptyStat();
  for (const c of cells) {
    if (c.h < 0 || c.h > 23 || c.d < 0 || c.d > 6 || !c.n) continue;
    const col = (c.d - weekStart + 7) % 7;
    rows[c.h][col] = c;
    hourTotals[c.h] = addStat(hourTotals[c.h], c);
    dayTotals[col] = addStat(dayTotals[col], c);
    total = addStat(total, c);
  }
  return { rows, hourTotals, dayTotals, total };
}

/** The number a slot shows for the chosen view, or null when it has nothing to show. */
export function metricOf(s: HourStat | null | undefined, metric: Metric): number | null {
  if (!s || !s.n) return null;
  if (metric === 'trades') return s.n;
  if (metric === 'net') return s.net;
  return s.win_rate === null ? null : s.win_rate * 100;
}

const GREEN = '34,197,94', GREEN2 = '16,185,129', RED = '239,68,68', RED2 = '244,63,94', VIOLET = '139,92,246', CYAN = '56,189,248', SLATE = '148,163,184';
const rgba = (rgb: string, a: number) => `rgba(${rgb},${a.toFixed(2)})`;
const glow = (from: string, to: string, t: number) => {          // t 0..1: a glossy two-colour wash, deeper the bigger t is
  const a = 0.2 + 0.62 * Math.sqrt(Math.max(0, Math.min(1, t)));
  return `linear-gradient(145deg, ${rgba(from, a)}, ${rgba(to, a * 0.5)})`;
};

/** What the colours are measured against: the busiest slot (trades), the biggest win or loss (net). The win rate is measured against break-even, so it needs no scale. */
export function scaleOf(m: Matrix, metric: Metric): number {
  let mx = 0;
  for (const row of m.rows) for (const s of row) if (s) mx = Math.max(mx, metric === 'trades' ? s.n : Math.abs(s.net));
  return mx;
}

/** The fill of one slot: violet to cyan for how many trades; green or red for profit or loss; green above and red below break-even for the win rate. Empty slots get none. */
export function cellBackground(s: HourStat | null | undefined, metric: Metric, scale: number, breakEven: number | null): string | undefined {
  if (!s || !s.n) return undefined;
  if (metric === 'trades') return glow(VIOLET, CYAN, scale ? s.n / scale : 0);
  if (metric === 'net') {
    if (!s.net) return `linear-gradient(145deg, ${rgba(SLATE, 0.22)}, ${rgba(SLATE, 0.1)})`;
    return s.net > 0 ? glow(GREEN, GREEN2, scale ? s.net / scale : 0) : glow(RED, RED2, scale ? -s.net / scale : 0);
  }
  if (s.win_rate === null) return `linear-gradient(145deg, ${rgba(SLATE, 0.22)}, ${rgba(SLATE, 0.1)})`;
  const diff = s.win_rate - (breakEven ?? 0.5);
  if (Math.abs(diff) < 0.005) return `linear-gradient(145deg, ${rgba(SLATE, 0.28)}, ${rgba(SLATE, 0.12)})`;
  return diff > 0 ? glow(GREEN, GREEN2, diff / 0.25) : glow(RED, RED2, -diff / 0.25);
}

export interface Pick { index: number; stat: HourStat }
export interface Insights {
  busiest: (Pick & { share: number }) | null;                       // the hour with the most trades
  best: Pick | null;                                                // the hour that made the most (needs MIN_PICK trades and a profit)
  worst: Pick | null;                                               // the hour that lost the most (needs MIN_PICK trades and a loss)
  bestDay: Pick | null; worstDay: Pick | null;                      // the same for the days (index = the column)
}

export function insightsOf(m: Matrix): Insights {
  let busiest: Insights['busiest'] = null, best: Pick | null = null, worst: Pick | null = null, bestDay: Pick | null = null, worstDay: Pick | null = null;
  m.hourTotals.forEach((s, i) => {
    if (!s.n) return;
    if (!busiest || s.n > busiest.stat.n) busiest = { index: i, stat: s, share: m.total.n ? s.n / m.total.n : 0 };
    if (s.n >= MIN_PICK && s.net > 0 && (!best || s.net > best.stat.net)) best = { index: i, stat: s };
    if (s.n >= MIN_PICK && s.net < 0 && (!worst || s.net < worst.stat.net)) worst = { index: i, stat: s };
  });
  m.dayTotals.forEach((s, i) => {
    if (s.n < MIN_PICK) return;
    if (s.net > 0 && (!bestDay || s.net > bestDay.stat.net)) bestDay = { index: i, stat: s };
    if (s.net < 0 && (!worstDay || s.net < worstDay.stat.net)) worstDay = { index: i, stat: s };
  });
  return { busiest, best, worst, bestDay, worstDay };
}

const pad = (h: number) => String(h).padStart(2, '0');
export const hourLabel = (h: number): string => `${pad(h)}:00`;
export const hourRange = (h: number): string => `${pad(h)}:00–${pad((h + 1) % 24)}:00`;
