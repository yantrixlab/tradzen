import { useQuery } from '@tanstack/react-query';
import { api, qs } from '../api';

/** One side of a strategy's rule: how much of it holds on the forming candle, and which conditions are still missing. */
interface Side { p: number; met: number; total: number; missing: string[] }
export interface Readiness {
  state: 'building' | 'ready' | 'blocked' | 'warming' | 'error'; lead: 'CALL' | 'PUT' | null; p: number; reason?: string; have?: number; need?: number; call?: Side; put?: Side;
}
export interface ReadinessMap { symbol: string; tf: number; forming: boolean; strategies: Record<string, Readiness> }

/** How close every running strategy is to a signal on the candle that is forming right now (as if it closed as it is). Refreshed every 2 s. */
export const useReadiness = (symbol: string, tf: number) => useQuery({
  queryKey: ['readiness', symbol, tf], enabled: !!symbol, refetchInterval: 2000, staleTime: 1000, retry: false,
  queryFn: () => api<ReadinessMap>(`/api/readiness${qs({ symbol, tf })}`),
});

const arrow = (d: string | null) => (d === 'CALL' ? '▲' : d === 'PUT' ? '▼' : '');

/** stage -> colours: far grey, building blue, near amber, almost orange, ready green (CALL) / red (PUT), blocked purple */
function view(r: Readiness | undefined): { label: string; pct: number; bar: string; text: string; pulse: boolean; tip: string } {
  if (!r) return { label: 'reading…', pct: 0, bar: 'bg-[#334155]', text: 'text-mut', pulse: false, tip: '' };
  if (r.state === 'warming') return { label: `warming up · ${r.have}/${r.need} candles`, pct: Math.min(100, (100 * (r.have ?? 0)) / (r.need || 1)), bar: 'bg-[#334155]', text: 'text-mut', pulse: false, tip: 'Not enough candles yet for this strategy.' };
  if (r.state === 'error') return { label: 'could not read', pct: 0, bar: 'bg-[#334155]', text: 'text-mut', pulse: false, tip: r.reason ?? '' };
  const side = r.lead === 'CALL' ? r.call : r.lead === 'PUT' ? r.put : undefined;
  const pct = Math.round(100 * r.p);
  if (r.state === 'ready') return { label: `READY ${arrow(r.lead)} ${r.lead} · if this candle closes as it is`, pct: 100, bar: r.lead === 'CALL' ? 'bg-up shadow-[0_0_10px_#22c55e]' : 'bg-down shadow-[0_0_10px_#ef4444]', text: r.lead === 'CALL' ? 'text-up' : 'text-down', pulse: true, tip: 'Every rule holds on the candle that is forming. It can still change until it closes.' };
  if (r.state === 'blocked') return { label: `${arrow(r.lead)} rules met, blocked: ${(r.reason ?? '').replace(/^filtered: /, '')}`.slice(0, 80), pct: 100, bar: 'bg-[#7c3aed]', text: 'text-[#a78bfa]', pulse: false, tip: r.reason ?? '' };
  const stage = pct >= 80 ? ['bg-[#fb923c]', 'text-[#fb923c]', true] : pct >= 60 ? ['bg-[#f59e0b]', 'text-[#f59e0b]', false] : pct >= 34 ? ['bg-[#38bdf8]', 'text-[#38bdf8]', false] : ['bg-[#475069]', 'text-mut', false];
  const conds = side && side.total && side.total !== 100 ? ` · ${side.met}/${side.total} conditions` : '';
  const miss = side?.missing.length ? ` Still missing: ${side.missing.join(' ; ')}.` : '';
  return { label: r.lead ? `${arrow(r.lead)} ${pct}%${conds}` : 'nothing yet', pct, bar: stage[0] as string, text: stage[1] as string, pulse: stage[2] as boolean, tip: `Share of the rule that holds right now (not a chance of winning).${miss}` };
}

export function ReadinessBar({ r }: { r: Readiness | undefined }) {
  const v = view(r);
  return (
    <div className="mt-1.5 max-w-[260px]" title={v.tip}>
      <div className={`mb-0.5 text-[11px] font-bold ${v.text}`}>{v.label}</div>
      <div className="h-1.5 overflow-hidden rounded-full bg-line"><div className={`h-full rounded-full transition-all duration-500 ${v.bar} ${v.pulse ? 'animate-pulse' : ''}`} style={{ width: `${v.pct}%` }} /></div>
    </div>
  );
}
