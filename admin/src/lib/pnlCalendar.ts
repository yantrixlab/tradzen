/** Pure helpers for the profit / loss calendar on My trades: month grids, the ranges the server is asked for, colours. Days are `YYYY-MM-DD` strings in the viewer's own calendar. */

export interface DayStat {
  n: number; wins: number; losses: number; draws: number; win_rate: number | null; staked: number; net: number; roi: number | null;
  best: number | null; worst: number | null; top_pair: { symbol: string; net: number } | null;
}
export interface CalSummary extends Omit<DayStat, 'best' | 'worst' | 'top_pair'> {
  trading_days: number; green_days: number; red_days: number; flat_days: number;
  best_day: { date: string; net: number } | null; worst_day: { date: string; net: number } | null; avg_day: number | null;
  best_green_streak: number; best_red_streak: number; current_streak: { kind: 'green' | 'red' | null; n: number };
  first_day: string | null; last_day: string | null;
}
export interface PairStat { symbol: string; n: number; wins: number; losses: number; draws: number; win_rate: number | null; staked: number; net: number; roi: number | null }
export interface CalResp { days: Record<string, DayStat>; summary: CalSummary; pairs: PairStat[] }

export interface MonthKey { y: number; m: number }                       // m = 0..11

const pad = (x: number) => String(x).padStart(2, '0');
export const dayKey = (y: number, m: number, d: number): string => `${y}-${pad(m + 1)}-${pad(d)}`;
export const monthLabel = (k: MonthKey): string => new Date(k.y, k.m, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
export const sameMonth = (a: MonthKey, b: MonthKey): boolean => a.y === b.y && a.m === b.m;
export const addMonths = (k: MonthKey, n: number): MonthKey => { const d = new Date(k.y, k.m + n, 1); return { y: d.getFullYear(), m: d.getMonth() }; };
export const monthOf = (d: Date): MonthKey => ({ y: d.getFullYear(), m: d.getMonth() });

/** [from, to) in epoch seconds of one month, on the viewer's own midnights. */
export const monthRange = (k: MonthKey): { from: number; to: number } => ({ from: Math.floor(new Date(k.y, k.m, 1).getTime() / 1000), to: Math.floor(new Date(k.y, k.m + 1, 1).getTime() / 1000) });

/** The newest `count` months, newest first. */
export const monthsBack = (now: Date, count: number): MonthKey[] => Array.from({ length: count }, (_, i) => addMonths(monthOf(now), -i));

/** Every month from the one holding `first` to the one holding `last` (both `YYYY-MM-DD`), newest first, at most `cap`. */
export function monthsSpan(first: string, last: string, cap = 24): MonthKey[] {
  const f = /^(\d{4})-(\d{2})/.exec(first), l = /^(\d{4})-(\d{2})/.exec(last);
  if (!f || !l) return [];
  const lo = { y: +f[1], m: +f[2] - 1 }, hi = { y: +l[1], m: +l[2] - 1 };
  const out: MonthKey[] = [];
  for (let k = hi; (k.y > lo.y || (k.y === lo.y && k.m >= lo.m)) && out.length < cap; k = addMonths(k, -1)) out.push(k);
  return out;
}

/** A month as weeks of 7 cells (`YYYY-MM-DD`, or null outside the month). weekStart: 0 = Sunday first, 1 = Monday first. */
export function weeksOf(k: MonthKey, weekStart: 0 | 1): (string | null)[][] {
  const first = new Date(k.y, k.m, 1).getDay();                          // 0 = Sunday
  const lead = (first - weekStart + 7) % 7;
  const days = new Date(k.y, k.m + 1, 0).getDate();
  const cells: (string | null)[] = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => dayKey(k.y, k.m, i + 1))];
  while (cells.length % 7) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7));
}

export const weekdayNames = (weekStart: 0 | 1): string[] => {
  const base = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return [...base.slice(weekStart), ...base.slice(0, weekStart)];
};

/** Totals of one week of cells. */
export function weekTotal(week: (string | null)[], days: Record<string, DayStat>): { net: number; n: number; days: number } {
  let net = 0, n = 0, d = 0;
  for (const c of week) { const s = c ? days[c] : undefined; if (s) { net += s.net; n += s.n; d += 1; } }
  return { net: Math.round(net * 100) / 100, n, days: d };
}

/** Background of a day: green / red whose strength follows the size of the result against the biggest day shown (square root, so small days stay visible). */
export function shade(net: number, maxAbs: number): string {
  if (!net || !maxAbs) return 'rgba(148,163,184,0.16)';
  const a = 0.2 + 0.6 * Math.sqrt(Math.min(1, Math.abs(net) / maxAbs));
  return net > 0 ? `rgba(34,197,94,${a.toFixed(2)})` : `rgba(239,68,68,${a.toFixed(2)})`;
}

export const longDate = (key: string): string => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' }) : key;
};
