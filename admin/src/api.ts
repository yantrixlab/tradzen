// Thin typed-ish fetch wrapper around the backend API (same origin in production, proxied in `npm run dev`).
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
    headers: { 'X-Requested-With': 'qxs', ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },   // the header is the server's CSRF check for signed-in changes
    credentials: 'same-origin',
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && path !== '/api/auth/login' && path !== '/api/me') window.dispatchEvent(new Event('qxs-unauth'));         // the session ended: the app shows the sign-in page
  if (!res.ok) {
    let msg = await res.text();
    try {
      const j = JSON.parse(msg);
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* plain text */
    }
    throw new ApiError(msg || res.statusText, res.status);
  }
  return res.json() as Promise<T>;
}

export const qs = (params: Record<string, unknown>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};

// ---- minimal shapes of what the panel reads (the API returns more) ----
export interface Stats {
  total: number; calls: number; puts: number; skips: number; wins: number; losses: number; draws: number; pending: number; settled: number;
  accuracy: number | null; ci_low: number | null; ci_high: number | null; avg_strength: number | null; breakeven: number | null; expectancy: number | null;
}
/** What a one-step martingale would have done to a strategy's real settled signals (units = the first stake). */
export interface Mtg1 {
  steps: number; mult: number; signals: number; taken: number; skipped: number; wins: number; rescued: number; lost: number; draws: number; open: number;
  accuracy: number | null; net: number; flat_net: number;
}
export interface Strategy {
  id: number; key: string; name: string; kind: 'builtin' | 'custom'; description: string; status: string; version: number; tags: string[];
  stats: Stats; muted: boolean; running: boolean; evaluations: number; last_signal: number | null; error?: string[] | null; updated: number;
  mtg1?: Mtg1 | null; mtg1_pending?: boolean;
  owner_id?: number | null; audience?: 'admin' | 'optin' | 'everyone'; mine?: boolean;      // owner_id null = a shared strategy; audience: who receives a shared strategy's signals
}
export interface Signal {
  id: number; created: number; symbol: string; tf: number; basis_t: number; target_t: number; direction: 'CALL' | 'PUT' | 'SKIP'; score: number;
  strength: number; agree: number; tier: string; status: string; entry: number | null; exit: number | null; strategy_id: number; strategy_version: number;
  mode: string; reason: string; factors: { key: string; name: string; weight: number; vote: number; note: string }[];
  hour_utc?: number | null; session?: string | null; adx?: number | null; atr_ratio?: number | null; rsi?: number | null; trend_align?: number | null;
  margin_atr?: number | null; near_miss?: number | null;
  risk?: { tag: 'risky' | 'ok'; p_win: number | null; reasons: string[] } | null;      // the risk advisor's tag (only set for strategies whose backtest losses were studied)
  mtg1?: SignalMtg | null; mtg2?: SignalMtg | null;      // what this signal came to with a one-step / two-step martingale (settled signals only)
}
export interface SignalMtg { outcome: 'win' | 'mtg1' | 'mtg2' | 'lost' | 'draw' | 'open'; skip: boolean; pl: number | null }
export interface MtgSummary { steps: number; signals: number; taken: number; skipped: number; wins: number; rescued: number; lost: number; draws: number; open: number; accuracy: number | null; net: number; flat_net: number }
export interface SignalsMtg { settled: number; flat_accuracy: number | null; flat_net: number; m1: MtgSummary; m2: MtgSummary; capped: boolean }
export interface Candle { t: number; o: number; h: number; l: number; c: number; n: number }
