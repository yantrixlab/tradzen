import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, dateTime, prettyPair, tfName } from '../format';
import { Badge, Card, Empty, ErrorBox, Spinner, Stat } from './ui';

type Paused = { reason: string; since: number; auto: boolean } | null;
type Row = { uid: number; username: string; received: number; new: number; dup: number; conflict: number; rejected: number; batches: number; stored: number; paused: Paused };
type Dispute = { id: number; ts: number; uid: number; username: string; symbol: string; tf: number; candle_ts: number; stored: number[]; sent: number[]; stored_source: string };
type Want = { asset: string; name: string; tf: number; kind: 'gap' | 'depth'; from: number; to: number; missing: number };
type Overview = {
  enabled: boolean; stored_from_contributions: number; users: Row[]; disputes: Dispute[]; wants: Want[];
  limits: { batches_per_minute: number; rows_per_hour: number; global_rows_per_second: number; auto_pause_share: number; auto_pause_min_rows: number };
};

const n = (x: number) => x.toLocaleString();
const ohlc = (v: number[]) => (v.length === 4 ? `O ${v[0]} H ${v[1]} L ${v[2]} C ${v[3]}` : '–');

/** Shared candles: what every signed-in extension added to the one candle store (admin only). */
export function ContribPanel({ now }: { now: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['contrib'], queryFn: () => api<Overview>('/api/contrib'), refetchInterval: 20000 });
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ['contrib'] });
  const fail = (e: Error) => setErr(e.message);
  const pause = useMutation({ mutationFn: (id: number) => api(`/api/contrib/${id}/pause`, { method: 'POST', body: { reason: 'paused by the admin' } }), onSuccess: () => { setErr(null); refresh(); }, onError: fail });
  const resume = useMutation({ mutationFn: (id: number) => api(`/api/contrib/${id}/resume`, { method: 'POST', body: {} }), onSuccess: () => { setErr(null); refresh(); }, onError: fail });
  const revoke = useMutation({ mutationFn: (id: number) => api<{ removed: number }>(`/api/contrib/${id}`, { method: 'DELETE' }), onSuccess: (r) => { setErr(`${n(r.removed)} candles removed.`); refresh(); qc.invalidateQueries({ queryKey: ['coverage'] }); }, onError: fail });

  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const o = q.data;
  const week = o.users.reduce((a, u) => ({ received: a.received + u.received, new: a.new + u.new, dup: a.dup + u.dup, bad: a.bad + u.conflict + u.rejected }), { received: 0, new: 0, dup: 0, bad: 0 });

  return (
    <div className="space-y-4">
      {!o.enabled && <div className="rounded-lg border border-line bg-panel2 p-3 text-sm">Candle contributions are <b>switched off</b> on this server (<code>QXS_CONTRIB=off</code>); extensions are told not to send history.</div>}
      {err && <div className="rounded-lg border border-line bg-panel2 p-3 text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Candles from contributions" value={n(o.stored_from_contributions)} sub="stored in total, each tagged with who sent it" tone="up" />
        <Stat label="New in 7 days" value={n(week.new)} sub={`${n(week.dup)} were already known`} />
        <Stat label="Refused or disagreeing" value={n(week.bad)} sub={week.received ? `${((week.bad / week.received) * 100).toFixed(1)}% of ${n(week.received)} received` : 'nothing received yet'} tone={week.received && week.bad / week.received > 0.1 ? 'warn' : undefined} />
        <Stat label="Limits per account" value={`${o.limits.batches_per_minute} / min`} sub={`${n(o.limits.rows_per_hour)} candles an hour; server-wide ${n(o.limits.global_rows_per_second)} / s`} />
      </div>

      <Card title="How it works" right={<span className="note normal-case tracking-normal">first write wins</span>}>
        <p className="text-sm">Every signed-in extension offers the chart history its Quotex page loads. The server <b>only adds candles it does not have</b>; a stored candle is never changed by a contribution, and the price feed's own live candle may still replace a contributed one. Candles are checked (closed, aligned, sane open/high/low/close, no jumps, price level near what is stored); batches over the limits are refused, not queued. An account whose candles are mostly refused or in conflict ({Math.round(o.limits.auto_pause_share * 100)}% after {n(o.limits.auto_pause_min_rows)} candles) is paused automatically.</p>
      </Card>

      <Card title="Contributors (last 7 days)">
        {o.users.length === 0 ? <Empty>No contributions yet. They arrive when an ordinary user's extension loads a chart on a Quotex tab that is linked to their account.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Account', 'Received', 'New', 'Known', 'Conflicts', 'Refused', 'Stored now', 'State', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{o.users.map((u) => (
              <tr key={u.uid}>
                <td className="td font-medium">{u.username}</td><td className="td tabular-nums">{n(u.received)}</td><td className="td tabular-nums text-up">{n(u.new)}</td>
                <td className="td tabular-nums">{n(u.dup)}</td><td className={`td tabular-nums ${u.conflict ? 'text-warn' : ''}`}>{n(u.conflict)}</td><td className={`td tabular-nums ${u.rejected ? 'text-warn' : ''}`}>{n(u.rejected)}</td>
                <td className="td tabular-nums">{n(u.stored)}</td>
                <td className="td">{u.paused ? <Badge tone="down" title={u.paused.reason}>{u.paused.auto ? 'PAUSED (auto)' : 'PAUSED'}</Badge> : <Badge tone="up">ACTIVE</Badge>}</td>
                <td className="td whitespace-nowrap">
                  {u.paused ? <button className="btn btn-sm" onClick={() => resume.mutate(u.uid)}>Resume</button> : <button className="btn btn-sm" onClick={() => pause.mutate(u.uid)}>Pause</button>}{' '}
                  <button className="btn btn-sm" disabled={!u.stored} title="Delete every candle this account contributed (other candles are not touched)"
                    onClick={() => { if (confirm(`Delete the ${n(u.stored)} candles contributed by ${u.username}? This cannot be undone.`)) revoke.mutate(u.uid); }}>Remove candles</button>
                </td></tr>))}</tbody></table></div>
        )}
      </Card>

      <Card title="Wanted ranges" right={<span className="note normal-case tracking-normal">shown to contributors in the extension</span>}>
        {o.wants.length === 0 ? <Empty>Nothing is missing right now.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Pair', 'Why', 'From', 'To', 'Candles'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{o.wants.map((w) => (
              <tr key={`${w.asset}-${w.kind}-${w.from}`}><td className="td font-medium">{prettyPair(w.asset)}<div className="note">{tfName(w.tf)}</div></td>
                <td className="td">{w.kind === 'gap' ? <Badge tone="warn">hole in a live pair</Badge> : <Badge>older history</Badge>}</td>
                <td className="td note">{dateTime(w.from)}</td><td className="td note">{dateTime(w.to)}</td><td className="td tabular-nums">{n(w.missing)}</td></tr>))}</tbody></table></div>
        )}
      </Card>

      <Card title="Disagreements (newest 40)" right={<span className="note normal-case tracking-normal">a contributed candle that differs from the stored one; the stored one was kept</span>}>
        {o.disputes.length === 0 ? <Empty>None. Contributed candles have matched what the server already had.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['When', 'Account', 'Pair', 'Candle', 'Stored', 'Sent'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{o.disputes.map((d) => (
              <tr key={d.id}><td className="td note">{ago(d.ts, now)}</td><td className="td">{d.username}</td><td className="td">{prettyPair(d.symbol)} <span className="note">{tfName(d.tf)}</span></td><td className="td note">{dateTime(d.candle_ts)}</td>
                <td className="td note tabular-nums">{ohlc(d.stored)}<div>{d.stored_source}</div></td><td className="td note tabular-nums">{ohlc(d.sent)}</td></tr>))}</tbody></table></div>
        )}
      </Card>
    </div>
  );
}
