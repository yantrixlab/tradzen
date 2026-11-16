import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, Stats } from './api';
import { prettyPair, tfName } from './format';
import type { ChartArea, ChartBand, ChartMarker } from './components/chart/types';
import type { LiveSelection } from './hooks';
import { Badge } from './components/ui';

// ------------------------------------------------------------------ what /api/sessions/* returns
export type SignalStatus = 'headsup' | 'pending' | 'win' | 'loss' | 'draw' | 'void' | 'cancelled';
export interface SessionSignal {
  id: number; session_id: number; created: number; sent_at: number | null; symbol: string; tf: number; direction: 'CALL' | 'PUT'; entry_t: number; expiry_candles: number;
  area_lo: number | null; area_hi: number | null; area_label: string | null; note: string | null; tier: 'free' | 'premium'; payout: number | null; status: SignalStatus;
  entry: number | null; exit: number | null; settled_at: number | null;
}
export interface SessionStats extends Stats { voids: number; net: number }
export interface SessionInfo { id: number; title: string; tier: 'free' | 'premium'; status: 'live' | 'ended'; started: number; ended: number | null; follow_admin?: boolean }
export interface LiveSession {
  now: number; session: SessionInfo | null; locked?: boolean; pair?: { symbol: string | null; tf: number }; selected?: LiveSelection['selected'];
  signals?: SessionSignal[]; stats?: SessionStats | null; symbols?: LiveSelection['symbols']; alerts_on?: boolean;
}
export interface SessionSummary extends SessionInfo { locked: boolean; signals: number; stats: SessionStats | null }

/** The live session, polled the way the live chart is (the chart candles ride in the same answer). */
export const useLiveSession = () => useQuery({ queryKey: ['live-session'], queryFn: () => api<LiveSession>('/api/sessions/live'), refetchInterval: 1500, placeholderData: keepPreviousData });
export const useSessionHistory = () => useQuery({ queryKey: ['session-history'], queryFn: () => api<SessionSummary[]>('/api/sessions'), refetchInterval: 15000 });

// ------------------------------------------------------------------ colours and words
export const STATUS_COLOR: Record<SignalStatus, string> = {
  headsup: '#38bdf8', pending: '#a78bfa', win: '#22c55e', loss: '#f87171', draw: '#94a3b8', void: '#64748b', cancelled: '#64748b',
};
export const STATUS_WORD: Record<SignalStatus, string> = { headsup: 'HEADS-UP', pending: 'TAKE IT', win: 'WIN', loss: 'LOSS', draw: 'DRAW', void: 'VOID', cancelled: 'CANCELLED' };
export const clock = (t: number): string => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
export const priceText = (p: number | null | undefined): string => (p == null ? '–' : p >= 100 ? p.toFixed(2) : p.toFixed(5));
export const areaText = (s: Pick<SessionSignal, 'area_lo' | 'area_hi' | 'area_label'>): string =>
  s.area_lo == null ? '' : `${priceText(s.area_lo)} – ${priceText(s.area_hi)}${s.area_label ? ` (${s.area_label})` : ''}`;

/**
 * The marks of a session's signals on a chart of one pair and timeframe (the same for the admin console and the viewers):
 *  - an arrow on the candle the trade comes AFTER (the one that closes before the entry candle), labelled with what to do,
 *  - a shaded band on the entry candle (and the candles of a longer trade), coloured by state and carrying the result,
 *  - the price area the signal is taken from, running on to the right edge until the trade has ended.
 * Everything is placed by candle time (entry_t), never by the browser's clock.
 */
export function sessionOverlay(signals: SessionSignal[] | undefined, symbol: string | null | undefined, tf: number | null | undefined): { markers: ChartMarker[]; bands: ChartBand[]; areas: ChartArea[] } {
  const out = { markers: [] as ChartMarker[], bands: [] as ChartBand[], areas: [] as ChartArea[] };
  for (const s of signals ?? []) {
    if (s.status === 'cancelled' || s.symbol !== symbol || s.tf !== tf) continue;
    const dir: 1 | -1 = s.direction === 'CALL' ? 1 : -1;
    const col = STATUS_COLOR[s.status];
    const basis = s.entry_t - s.tf, last = s.entry_t + (s.expiry_candles - 1) * s.tf;
    const live = s.status === 'headsup' || s.status === 'pending';
    const word = live ? (s.status === 'headsup' ? `${s.direction} soon` : `${s.direction} next`) : STATUS_WORD[s.status];
    out.markers.push({ t: basis, dir, color: col, label: word, tip: `${s.direction} ${prettyPair(s.symbol)} · candle ${clock(s.entry_t)}${s.area_lo != null ? ` · area ${areaText(s)}` : ''}${s.note ? ` · ${s.note}` : ''}` });
    if (live) out.bands.push({ t0: basis, t1: basis, color: col, opacity: 0.1 });
    out.bands.push({ t0: s.entry_t, t1: last, color: col, opacity: live ? 0.18 : 0.24, label: live ? `${s.direction} ${clock(s.entry_t)}` : `${s.direction} ${STATUS_WORD[s.status]}` });
    if (s.area_lo != null && s.area_hi != null) {
      out.areas.push({ t0: basis - 6 * s.tf, t1: last + s.tf - 1, lo: s.area_lo, hi: s.area_hi, color: dir > 0 ? '#22c55e' : '#ef4444', open: live,
        dashed: !live, label: `${s.direction} area${s.area_label ? ` · ${s.area_label}` : ''}` });
    }
  }
  return out;
}

// ------------------------------------------------------------------ small shared views
export function StatusChip({ status }: { status: SignalStatus }) {
  const tone = status === 'win' ? 'up' : status === 'loss' ? 'down' : status === 'pending' ? 'acc' : status === 'headsup' ? 'warn' : 'mut';
  return <Badge tone={tone}>{STATUS_WORD[status]}</Badge>;
}

/** The session's signals, newest first: when, pair, direction, area, state / result. `actions` adds per-row buttons (the admin console). */
export function SignalList({ signals, actions }: { signals: SessionSignal[]; actions?: (s: SessionSignal) => React.ReactNode }) {
  if (!signals.length) return <div className="note p-3 text-center">No signals in this session yet.</div>;
  return (
    <div className="overflow-x-auto"><table className="w-full">
      <thead><tr>{['Candle', 'Pair', 'Signal', 'Area', 'Note', 'State', ...(actions ? [''] : [])].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
      <tbody>{[...signals].reverse().map((s) => (
        <tr key={s.id} className={s.status === 'cancelled' ? 'opacity-50' : ''}>
          <td className="td tabular-nums">{clock(s.entry_t)} <span className="note">{tfName(s.tf)}{s.expiry_candles > 1 ? ` ×${s.expiry_candles}` : ''}</span></td>
          <td className="td">{prettyPair(s.symbol)}</td>
          <td className={`td font-bold ${s.direction === 'CALL' ? 'text-up' : 'text-down'}`}>{s.direction === 'CALL' ? '▲ CALL' : '▼ PUT'}</td>
          <td className="td tabular-nums text-xs">{areaText(s) || '–'}</td>
          <td className="td text-xs">{s.note || ''}</td>
          <td className="td"><StatusChip status={s.status} />{s.entry != null && s.exit != null && <span className="note ml-1 tabular-nums">{priceText(s.entry)} → {priceText(s.exit)}</span>}</td>
          {actions && <td className="td whitespace-nowrap">{actions(s)}</td>}
        </tr>))}</tbody>
    </table></div>
  );
}

/** Wins / losses / draws / net of a session in one line. */
export function SessionTally({ stats }: { stats: SessionStats | null | undefined }) {
  if (!stats) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
      <span><b className="tabular-nums text-up">{stats.wins}</b> win</span>
      <span><b className="tabular-nums text-down">{stats.losses}</b> loss</span>
      {stats.draws > 0 && <span><b className="tabular-nums">{stats.draws}</b> draw</span>}
      {stats.pending > 0 && <span><b className="tabular-nums text-acc">{stats.pending}</b> running</span>}
      {stats.voids > 0 && <span className="note">{stats.voids} void</span>}
      <span className={`font-semibold tabular-nums ${stats.net > 0 ? 'text-up' : stats.net < 0 ? 'text-down' : ''}`} title="In units of the stake, at the payout of each signal">net {stats.net >= 0 ? '+' : ''}{stats.net.toFixed(2)}</span>
    </div>
  );
}
