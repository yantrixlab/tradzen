import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago } from '../format';
import { Badge, Card, Empty, ErrorBox, Spinner, Stat } from './ui';

type Paused = { reason: string; since: number; auto: boolean } | null;
type Row = { uid: number; username: string; requests: number; applied: number; bad: number; late: number; shadow_ok: number; shadow_bad: number; dropped: number; stored: number; paused: Paused; holding: string[] };
type Holder = { symbol: string; name: string; uid: number; username: string; since: number; age: number; ticks: number };
type Overview = {
  enabled: boolean; holders: Holder[]; feed_pairs: { symbol: string; name: string; age: number }[]; users: Row[]; candles_from_accounts: number;
  limits: { ticks_per_second: number; max_pairs_per_account: number; lease_seconds: number; feed_priority_seconds: number; jump_limit: number; auto_pause_share: number };
};

const n = (x: number) => x.toLocaleString();

/** Live prices from ordinary accounts: who holds which pair (a lease), what the price feed has, and each account's counters (admin only). */
export function FeedPanel({ now }: { now: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['livefeed'], queryFn: () => api<Overview>('/api/livefeed'), refetchInterval: 5000 });
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ['livefeed'] });
  const fail = (e: Error) => setErr(e.message);
  const pause = useMutation({ mutationFn: (id: number) => api(`/api/livefeed/${id}/pause`, { method: 'POST', body: { reason: 'paused by the admin' } }), onSuccess: () => { setErr(null); refresh(); }, onError: fail });
  const resume = useMutation({ mutationFn: (id: number) => api(`/api/livefeed/${id}/resume`, { method: 'POST', body: {} }), onSuccess: () => { setErr(null); refresh(); }, onError: fail });
  const revoke = useMutation({ mutationFn: (id: number) => api<{ removed: number }>(`/api/livefeed/${id}`, { method: 'DELETE' }), onSuccess: (r) => { setErr(`${n(r.removed)} candles removed.`); refresh(); qc.invalidateQueries({ queryKey: ['coverage'] }); }, onError: fail });

  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const o = q.data;
  const week = o.users.reduce((a, u) => ({ applied: a.applied + u.applied, bad: a.bad + u.bad, sOk: a.sOk + u.shadow_ok, sBad: a.sBad + u.shadow_bad }), { applied: 0, bad: 0, sOk: 0, sBad: 0 });
  const agree = week.sOk + week.sBad ? week.sOk / (week.sOk + week.sBad) : null;

  return (
    <div className="space-y-4">
      {!o.enabled && <div className="rounded-lg border border-line bg-panel2 p-3 text-sm">Live prices from ordinary accounts are <b>switched off</b> on this server (<code>QXS_LIVE_FEED=off</code>); only your price-feed tab streams.</div>}
      {err && <div className="rounded-lg border border-line bg-panel2 p-3 text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Pairs held by accounts" value={String(o.holders.length)} sub={`${o.feed_pairs.length} streamed by your price feed right now`} tone={o.holders.length ? 'up' : undefined} />
        <Stat label="Prices used (7 days)" value={n(week.applied)} sub={`${n(week.bad)} refused`} tone={week.applied && week.bad / (week.applied + week.bad) > 0.1 ? 'warn' : undefined} />
        <Stat label="Spot-checks agree" value={agree == null ? '–' : `${(agree * 100).toFixed(1)}%`} sub={`${n(week.sOk + week.sBad)} compared with the applied prices`} tone={agree != null && agree < 0.95 ? 'warn' : undefined} />
        <Stat label="Candles from accounts" value={n(o.candles_from_accounts)} sub="built from an account's ticks (removable per account)" />
      </div>

      <Card title="How it works" right={<span className="note normal-case tracking-normal">one account per pair</span>}>
        <p className="text-sm">Any signed-in extension can stream the live prices of the pairs open in its Quotex tab, but <b>one account holds each pair at a time</b> (a lease): everybody else's prices for it are only compared with the held ones, never applied. <b>Your price-feed tab always has priority</b>: while it streams a pair there is no lease on it, and a holder steps aside the moment it returns. A holder that goes silent for {o.limits.lease_seconds} s loses the pair; the next account that offers ticks takes it (the engine drops a candle with a hole, so a hand-over can lose one candle but not make a wrong one). Every price is checked (not old, not in the future, newer than the last, within {Math.round(o.limits.jump_limit * 100)}% of the previous one); {o.limits.max_pairs_per_account} pairs and {o.limits.ticks_per_second} prices a second per account at most. An account that is mostly refused ({Math.round(o.limits.auto_pause_share * 100)}%) or keeps disagreeing with the others is paused automatically.</p>
      </Card>

      <Card title="Pairs streamed now">
        {o.holders.length === 0 && o.feed_pairs.length === 0 ? <Empty>No pair is streaming right now.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Pair', 'Source', 'Since', 'Last price', 'Prices'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>
              {o.feed_pairs.map((p) => <tr key={`f-${p.symbol}`}><td className="td font-medium">{p.name}</td><td className="td"><Badge tone="up">PRICE FEED</Badge></td><td className="td note">–</td><td className="td note">{p.age.toFixed(0)} s ago</td><td className="td note">–</td></tr>)}
              {o.holders.map((h) => <tr key={`h-${h.symbol}`}><td className="td font-medium">{h.name}</td><td className="td"><Badge>{h.username}</Badge></td><td className="td note">{ago(h.since, now)}</td><td className="td note">{h.age.toFixed(0)} s ago</td><td className="td tabular-nums">{n(h.ticks)}</td></tr>)}
            </tbody></table></div>
        )}
      </Card>

      <Card title="Accounts (last 7 days)">
        {o.users.length === 0 ? <Empty>No account has streamed prices yet. It happens when an ordinary user has a pair open that your price feed does not stream.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Account', 'Holding', 'Used', 'Refused', 'Late', 'Spot-check ✓ / ✗', 'Dropped', 'Candles', 'State', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{o.users.map((u) => (
              <tr key={u.uid}>
                <td className="td font-medium">{u.username}</td><td className="td note">{u.holding.length ? u.holding.join(', ') : '–'}</td>
                <td className="td tabular-nums text-up">{n(u.applied)}</td><td className={`td tabular-nums ${u.bad ? 'text-warn' : ''}`}>{n(u.bad)}</td><td className="td tabular-nums">{n(u.late)}</td>
                <td className="td tabular-nums">{n(u.shadow_ok)} / <span className={u.shadow_bad ? 'text-warn' : ''}>{n(u.shadow_bad)}</span></td><td className="td tabular-nums">{n(u.dropped)}</td>
                <td className="td tabular-nums">{n(u.stored)}</td>
                <td className="td">{u.paused ? <Badge tone="down" title={u.paused.reason}>{u.paused.auto ? 'PAUSED (auto)' : 'PAUSED'}</Badge> : <Badge tone="up">ACTIVE</Badge>}</td>
                <td className="td whitespace-nowrap">
                  {u.paused ? <button className="btn btn-sm" onClick={() => resume.mutate(u.uid)}>Resume</button> : <button className="btn btn-sm" onClick={() => pause.mutate(u.uid)}>Pause</button>}{' '}
                  <button className="btn btn-sm" disabled={!u.stored} title="Delete every candle built from this account's prices (the price feed's candles are not touched)"
                    onClick={() => { if (confirm(`Delete the ${n(u.stored)} candles built from the prices of ${u.username}? This cannot be undone.`)) revoke.mutate(u.uid); }}>Remove candles</button>
                </td></tr>))}</tbody></table></div>
        )}
      </Card>
    </div>
  );
}
