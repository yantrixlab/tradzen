import { useCallback, useState, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, Stats, Strategy } from './api';

export interface LiveState {
  now: number;
  ingest: { batches: number; ticks: number; last_recv: number | null; hist?: { asset: string; rows: number; tf?: number; result: string; at: number; new?: number; from?: number; to?: number }[] };
  symbols: { symbol: string; name: string; market: string; category: string; payout: number | null; open: boolean; streaming: boolean; last_price: number | null }[];
  stats: Stats;
  recent: import('./api').Signal[];
  feed_events: { t: number; e: string; d: string | null }[];
}

export interface Me { user: { id: number; username: string; role: 'admin' | 'editor' | 'trader'; must_change_pw: boolean; plan_id: number | null; plan_until: number | null; qx_linked?: boolean; qx_hint?: string | null; qx_at?: number | null }; quotex_link?: boolean; auth_enabled: boolean; via: string | null; features: Record<string, boolean | number | null>; plan: { id: number; name: string } }
/** Who is signed in (on a local server with sign-in switched off, the local admin). A 401 means: show the sign-in page. */
export const useMe = () => useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/api/me'), retry: false, staleTime: 60000, refetchOnWindowFocus: true });

export const useLiveState = () => useQuery({ queryKey: ['state'], queryFn: () => api<LiveState>('/api/state'), refetchInterval: 2000 });
export const useStrategies = () => useQuery({ queryKey: ['strategies'], queryFn: () => api<Strategy[]>('/api/strategies'), refetchInterval: 5000 });
export const useCatalog = () => useQuery({ queryKey: ['catalog'], queryFn: () => api<Catalog>('/api/spec/catalog'), staleTime: Infinity });
export const useCoverage = () => useQuery({ queryKey: ['coverage'], queryFn: () => api<Coverage[]>('/api/data/coverage'), staleTime: 15000 });

export interface Catalog {
  indicators: Record<string, { defaults: Record<string, number>; fields: string[] | null }>;
  fn_factors: Record<string, Record<string, number>>;
  filters: Record<string, Record<string, unknown>>;
  base_names: string[];
  functions: Record<string, number[]>;
  timeframes: number[];
  statuses: string[];
}
export interface GapReport { symbol: string; tf: number; candles: number; gaps: number; missing: number; holes: { after: number; before: number; missing: number }[] }
export const useGaps = (symbol: string | null, tf: number) =>
  useQuery({ queryKey: ['gaps', symbol, tf], enabled: !!symbol, queryFn: () => api<GapReport>(`/api/data/gaps?symbol=${encodeURIComponent(symbol ?? '')}&tf=${tf}&limit=100`) });
export interface PlanPair { symbol: string; name: string; market: string; payout: number; open: boolean; streaming: boolean; candles: number; first: number | null; last: number | null; age_s: number | null; need: number; scroll_to: number | null; done: boolean }
export interface CollectionPlan {
  tf: number; min_payout: number; target_total: number; per_pair_target: number; have_total: number; progress: number; pairs_total: number; pairs_done: number;
  streaming_pairs: number; live_rate_per_hour: number; eta_hours_live_only: number | null; pairs: PlanPair[];
}
export const usePlan = (minPayout: number, target: number, perPair: number) =>
  useQuery({ queryKey: ['plan', minPayout, target, perPair], queryFn: () => api<CollectionPlan>(`/api/data/plan?min_payout=${minPayout}&target_total=${target}&per_pair_target=${perPair}`), refetchInterval: 5000 });
export interface SentPair { symbol: string; name: string; market: string; payout: number | null; open: boolean; samples: number; first: number | null; last: number | null; age_s: number | null; recent: number; receiving: boolean }
export interface SentCoverage { total_samples: number; pairs_total: number; pairs_with_data: number; pairs_receiving: number; span_hours: number; samples_last_window: number; window_s: number; pairs: SentPair[] }
export interface SentBucket { lo: number; hi: number; n: number; up_rate: number | null; z: number | null; first_half: number | null; second_half: number | null; n_first: number; n_second: number; enough: boolean; stable: boolean | null }
export interface SentStudy { tf: number; samples_total: number; matched: number; pairs: number; buckets: SentBucket[]; edge_found: boolean; note: string | null; needed_per_bucket: number; min_payout: number | null }
export const useSentiment = () => useQuery({ queryKey: ['sentiment'], queryFn: () => api<SentCoverage>('/api/data/sentiment'), refetchInterval: 5000 });
export const useSentimentStudy = (minPayout: number) =>
  useQuery({ queryKey: ['sentiment-study', minPayout], queryFn: () => api<SentStudy>(`/api/data/sentiment/study${minPayout ? `?min_payout=${minPayout}` : ''}`), refetchInterval: 30000 });
export interface ResearchModel {
  window: number; model: string; auc_train: number; auc_test: number; ci_low: number; ci_high: number; band: number; p_adj: number; verdict: string; reason: string;
  top10: { n: number; win: number | null; z_vs_breakeven: number | null }; top2: { n: number; win: number | null; z_vs_breakeven: number | null };
}
export interface ResearchResult {
  verdict: string; summary: string; tried: number; models: ResearchModel[]; control: { auc_test: number; ok: boolean }; best: { window: number; model: string; auc_test: number; verdict: string };
  data: { rows: number; pairs: number; train: number; test: number; split_t: number; first_t: number; last_t: number; up_share_test: number };
  config: { min_auc: number; min_test: number; alpha: number; windows: number[]; breakeven: number };
  sentiment: { matched: number; edge_found: boolean; needed_per_bucket: number } | null;
}
export interface ResearchRow { id: number; created: number; finished: number | null; source: string; status: string; progress: number; error: string | null; verdict: string | null; best_auc: number | null; rows: number | null; test: number | null; summary: string | null }
export interface ResearchRun extends ResearchRow { result: ResearchResult | null }
export interface ResearchStatus { hour_utc: number | null; running: boolean; running_id: number | null; next_run: number | null; due: boolean; last_run: number | null }
export const useResearchStatus = () => useQuery({ queryKey: ['research-status'], queryFn: () => api<ResearchStatus>('/api/research/status'), refetchInterval: 4000 });
export const useResearchList = () => useQuery({ queryKey: ['research'], queryFn: () => api<ResearchRow[]>('/api/research'), refetchInterval: 5000 });
export const useResearchRun = (id: number | null) => useQuery({ queryKey: ['research-run', id], enabled: id !== null, queryFn: () => api<ResearchRun>(`/api/research/${id}`),
  refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false) });
export interface Coverage { symbol: string; tf: number; candles: number; first: number; last: number; expected: number; missing: number; completeness: number }

export interface SettingsView { alerts_enabled: boolean; can_resume: boolean }
export interface PresetList { builtin: { name: string; description: string }[]; custom: { name: string; count: number }[] }
export const useSettings = () => useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsView>('/api/settings'), refetchInterval: 5000 });
export const usePresets = () => useQuery({ queryKey: ['presets'], queryFn: () => api<PresetList>('/api/presets') });

export interface BoardRow {
  id: number; key: string; name: string; status: string; direction: 'CALL' | 'PUT' | 'SKIP'; tier: string; strength: number; score: number; agree: number; reason: string; warming: boolean;
  factors: { key: string; name: string; weight: number; vote: number; note: string }[];
}
export interface Board { symbol: string; tf: number; candles: number; bridged: boolean; calls: number; puts: number; neutral: number; strategies: BoardRow[] }

/** Why no signals are coming (worked out by the server): reasons worst first, each with what to do. `paused` = signals are on hold right now. */
export interface FeedHealth {
  paused: boolean;
  reasons: { level: 'bad' | 'warn' | 'info'; code: string; title: string; detail: string; fix: string | null; have?: number; need?: number }[];
}

export interface LiveSelection {
  now: number; alerts_enabled: boolean;
  health?: FeedHealth;
  symbols: LiveState['symbols'];
  selected: null | {
    symbol: string; tf: number; candles: import('./api').Candle[]; forming: import('./api').Candle | null; last_price: number | null; offset: number;
    sentiment: { pct: number } | null; gap: { missing: number; before: number } | null;
  };
}

/** useState that survives remounts and reloads (browser storage is optional: failures are ignored) */
export function usePersisted<T>(key: string, initial: T): [T, (v: T) => void] {
  const [val, setVal] = useState<T>(() => {
    try { const raw = localStorage.getItem(`qxs-admin-${key}`); return raw === null ? initial : (JSON.parse(raw) as T); } catch { return initial; }
  });
  const set = (v: T) => { setVal(v); try { localStorage.setItem(`qxs-admin-${key}`, JSON.stringify(v)); } catch { /* storage unavailable */ } };
  return [val, set];
}

/** number of pairs that are streaming right now, per market */
export const streamingCount = (symbols: LiveState['symbols'], market: 'OTC' | 'LIVE'): number => symbols.filter((s) => s.market === market && s.streaming).length;

export const strategyName = (list: Strategy[] | undefined, id: number | null | undefined): string => list?.find((s) => s.id === id)?.name ?? (id ? `#${id}` : '–');

// ---- account balance + currency (as the extension last saw them on Quotex) and money formatting that follows the account's own currency ----
export interface Account { live: number | null; demo: number | null; live_at: number | null; demo_at: number | null; currency: { code: string | null; symbol: string | null } | null; updated: number | null }
export const useAccount = () => useQuery({ queryKey: ['account'], queryFn: () => api<Account>('/api/account'), refetchInterval: 4000 });

/** ₹ for INR, $ for USD, R$ for BRL ...: from the ISO code when there is one, else the symbol the broker sent; '' while the currency is not known yet */
export function currencySymbol(c: Account['currency'] | undefined): string {
  if (!c) return '';
  if (c.code) {
    try {
      const p = new Intl.NumberFormat('en', { style: 'currency', currency: c.code, currencyDisplay: 'narrowSymbol' }).formatToParts(0).find((x) => x.type === 'currency');
      if (p && p.value !== c.code) return p.value;
    } catch { /* not a currency code the browser knows */ }
  }
  return c.symbol ?? (c.code ? c.code + ' ' : '');
}

export function formatMoney(x: number | null | undefined, symbol: string, sign = false, code?: string | null): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  const body = Math.abs(x).toLocaleString(code === 'INR' ? 'en-IN' : 'en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${x < 0 ? '−' : sign && x > 0 ? '+' : ''}${symbol}${body}`;
}

// ---- show / hide amounts (like a banking app): hidden until the eye is clicked; one switch for every money value in the admin, remembered in this browser ----
const AMOUNTS_KEY = 'qxs-admin-amounts-visible';
let amountsVisible = (() => { try { return localStorage.getItem(AMOUNTS_KEY) === '1'; } catch { return false; } })();
const amountSubs = new Set<() => void>();
export function useAmountsVisible(): [boolean, () => void] {
  const visible = useSyncExternalStore((cb) => { amountSubs.add(cb); return () => { amountSubs.delete(cb); }; }, () => amountsVisible);
  const toggle = useCallback(() => {
    amountsVisible = !amountsVisible;
    try { localStorage.setItem(AMOUNTS_KEY, amountsVisible ? '1' : '0'); } catch { /* storage unavailable: still works for this page view */ }
    amountSubs.forEach((f) => f());
  }, []);
  return [visible, toggle];
}
export const HIDDEN_AMOUNT = '••••';

/** money(x, sign?) -> "₹1,234.50" / "+$0.92" / "−€1.00" in the currency of the connected Quotex account; "••••" while amounts are hidden */
export function useMoney(): (x: number | null | undefined, sign?: boolean) => string {
  const { data } = useAccount();
  const [visible] = useAmountsVisible();
  const symbol = currencySymbol(data?.currency), code = data?.currency?.code;
  return useCallback((x: number | null | undefined, sign = false) => (visible || x === null || x === undefined ? formatMoney(x, symbol, sign, code) : HIDDEN_AMOUNT), [visible, symbol, code]);
}
