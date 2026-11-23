/** Money management page: the shapes the server sends (backend/app/moneymgmt.py) and the small pure helpers the page uses. Unit-tested in moneyMgmt.test.ts. */

export type MmStatus = 'go' | 'careful' | 'stop';
export type MmKind = 'base' | 'recovery' | 'brake' | 'win_streak' | 'manual' | 'cooldown' | 'loss_limit' | 'streak' | 'target' | 'small_balance' | 'no_balance';

export interface MmSettings {
  account: 'real' | 'demo'; base_pct: number; max_pct: number; min_stake: number;
  recovery: { on: boolean; spread: number }; loss: { on: boolean; pct: number }; target: { on: boolean; pct: number }; streak: { on: boolean; max: number };
}
export interface MmAdvice { stake: number | null; status: MmStatus; kind: MmKind; headline: string; reason: string; reasons: string[]; base: number | null; cap: number | null; room: number | null; debt: number }
export interface MmWhat { result: number; stake: number | null; status: MmStatus; kind: MmKind; headline: string; reason: string }
export interface MmRecent { id: string; status: 'win' | 'loss' | 'draw'; profit: number | null; amount: number | null; symbol: string | null; t: number }
export interface MmLadderRow { losses: number; stake: number; status: MmStatus; kind: MmKind; pct: number; lost_before: number; win_net: number | null }
export interface MoneyMgmt {
  settings: MmSettings; account: 'real' | 'demo'; now: number; day: string; currency: { code: string | null; symbol: string | null } | null;
  balance: { value: number | null; at: number | null; start: number | null; demo: number | null; real: number | null };
  advice: MmAdvice;
  today: { net: number; trades: number; wins: number; losses: number; draws: number; staked: number; peak: number; low: number; drawdown: number; running: number; open_risk: number };
  streak: { kind: 'win' | 'loss' | null; n: number };
  limits: { loss: { on: boolean; limit: number | null; used: number; pct: number | null }; target: { on: boolean; amount: number | null; got: number; pct: number | null }; streak: { on: boolean; max: number; n: number } };
  edge: { payout: number; break_even: number; win_rate: number | null; n: number; enough: boolean };
  recent: MmRecent[]; ladder: MmLadderRow[]; whatif: { loss: MmWhat; win: MmWhat } | null; warnings: { kind: string; text: string }[];
}

export const DEFAULT_SETTINGS: MmSettings = {
  account: 'real', base_pct: 1, max_pct: 3, min_stake: 1,
  recovery: { on: true, spread: 3 }, loss: { on: true, pct: 5 }, target: { on: true, pct: 5 }, streak: { on: true, max: 4 },
};

export interface Theme { color: string; glow: string; icon: string; label: string; tone: 'up' | 'warn' | 'down' | 'mut' }
const UP = '#22c55e', WARN = '#f59e0b', DOWN = '#ef4444', MUT = '#8a93b2';

/** Colour and wording of the big card: green = go, amber = careful, red = stop. Reaching the daily target is a stop, but a happy one (green); no balance yet is neutral. */
export function theme(status: MmStatus, kind: MmKind): Theme {
  if (kind === 'no_balance') return { color: MUT, glow: 'rgba(138,147,178,.16)', icon: '…', label: 'WAITING', tone: 'mut' };
  if (kind === 'target') return { color: UP, glow: 'rgba(34,197,94,.22)', icon: '🎯', label: 'STOP - KEEP THE WIN', tone: 'up' };
  if (status === 'stop') return { color: DOWN, glow: 'rgba(239,68,68,.24)', icon: '⛔', label: 'STOP FOR TODAY', tone: 'down' };
  if (status === 'careful') return { color: WARN, glow: 'rgba(245,158,11,.2)', icon: '!', label: 'CAREFUL', tone: 'warn' };
  return { color: UP, glow: 'rgba(34,197,94,.2)', icon: '✓', label: 'GO', tone: 'up' };
}

/** How full a risk meter is: green below half, amber up to 80%, red beyond. */
export function meterLevel(p: number | null | undefined): 'ok' | 'near' | 'hot' {
  if (p === null || p === undefined) return 'ok';
  return p >= 0.8 ? 'hot' : p >= 0.5 ? 'near' : 'ok';
}
export const LEVEL_COLOR = { ok: UP, near: WARN, hot: DOWN } as const;

/** "3 losses in a row" / "2 wins in a row" / "No streak yet". */
export function streakText(s: { kind: 'win' | 'loss' | null; n: number }): string {
  if (!s.kind || !s.n) return 'No streak yet';
  return `${s.n} ${s.kind === 'win' ? (s.n === 1 ? 'win' : 'wins') : s.n === 1 ? 'loss' : 'losses'} in a row`;
}

/** The recent results as dots, oldest first, at most `max` of them: the colour class and the label each dot needs. */
export function dots(recent: MmRecent[], max = 20): { id: string; cls: string; label: string }[] {
  return recent.slice(-max).map((r) => ({
    id: r.id, cls: r.status === 'win' ? 'bg-up' : r.status === 'loss' ? 'bg-down' : 'bg-mut',
    label: `${r.status === 'win' ? 'Won' : r.status === 'loss' ? 'Lost' : 'Draw'}${r.symbol ? ' ' + r.symbol.replace('_otc', ' OTC') : ''}`,
  }));
}

/** The same limits the server applies, so the form shows what will really be saved (the server clamps again). */
export function clampSettings(s: MmSettings): MmSettings {
  const c = (v: number, lo: number, hi: number, d: number) => (Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);
  const base = c(s.base_pct, 0.1, 10, 1);
  return {
    account: s.account === 'demo' ? 'demo' : 'real', base_pct: base, max_pct: Math.max(base, c(s.max_pct, 0.1, 10, 3)), min_stake: c(s.min_stake, 0.01, 1e6, 1),
    recovery: { on: !!s.recovery.on, spread: Math.round(c(s.recovery.spread, 2, 6, 3)) },
    loss: { on: !!s.loss.on, pct: c(s.loss.pct, 0.5, 50, 5) }, target: { on: !!s.target.on, pct: c(s.target.pct, 0.5, 100, 5) },
    streak: { on: !!s.streak.on, max: Math.round(c(s.streak.max, 2, 10, 4)) },
  };
}

export const sameSettings = (a: MmSettings | null, b: MmSettings | null): boolean => !!a && !!b && JSON.stringify(a) === JSON.stringify(b);

/** What a share of the balance comes to, for the hints next to the inputs; null while the balance is unknown. */
export const share = (balance: number | null | undefined, pct: number): number | null => (balance ? (balance * pct) / 100 : null);

/** The stake as a share of the balance, "1.3%" (one decimal), or "–". */
export const stakePct = (stake: number | null | undefined, balance: number | null | undefined): string =>
  stake && balance ? `${((stake / balance) * 100).toFixed(stake / balance < 0.1 ? 1 : 0)}%` : '–';

/** One-tap starting points for the settings form (the person can still change every number afterwards). */
export const PRESETS: { key: string; label: string; hint: string; s: Omit<MmSettings, 'account' | 'min_stake'> }[] = [
  { key: 'gentle', label: 'Gentle', hint: 'Smallest stakes, quick to stop', s: { base_pct: 0.5, max_pct: 1.5, recovery: { on: true, spread: 4 }, loss: { on: true, pct: 3 }, target: { on: true, pct: 3 }, streak: { on: true, max: 3 } } },
  { key: 'balanced', label: 'Balanced', hint: 'The default', s: { base_pct: 1, max_pct: 3, recovery: { on: true, spread: 3 }, loss: { on: true, pct: 5 }, target: { on: true, pct: 5 }, streak: { on: true, max: 4 } } },
  { key: 'firm', label: 'Firm', hint: 'Bigger stakes, still capped', s: { base_pct: 2, max_pct: 5, recovery: { on: true, spread: 3 }, loss: { on: true, pct: 8 }, target: { on: true, pct: 8 }, streak: { on: true, max: 5 } } },
];

/** The preset the settings match exactly (the account and the smallest stake do not count), or null. */
export const presetOf = (s: MmSettings): string | null => {
  const { account: _a, min_stake: _m, ...rest } = s;                                 // eslint-disable-line @typescript-eslint/no-unused-vars
  return PRESETS.find((p) => JSON.stringify(p.s) === JSON.stringify(rest))?.key ?? null;
};
