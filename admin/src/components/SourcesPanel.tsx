import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, Spinner, Stat } from './ui';

interface Feeder { uid: number; username: string; agent: string; worker: boolean; age: number; alive: boolean; ticks: number; batches: number; pairs_live: number; since: number }
interface Sources {
  switches: { contrib: { on: boolean; default: boolean }; leases: { on: boolean; default: boolean } };
  feeders: Feeder[]; streaming: { feed: number; lease: number; other: number }; total_streaming: number;
  worker: { seen: boolean; alive: boolean; age: number | null; pairs_live: number }; ready_to_switch_off: boolean; stored_from_accounts: number;
}
const n = (x: number) => x.toLocaleString();

/** Who feeds the market right now (the admin's tab, the server's feed worker) and the two switches for what ordinary accounts may send. Admin only. */
export function SourcesPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['feed-sources'], queryFn: () => api<Sources>('/api/platform-admin/sources'), refetchInterval: 5000 });
  const [draft, setDraft] = useState<{ contrib: boolean; leases: boolean } | null>(null);
  const save = useMutation({
    mutationFn: (d: { contrib: boolean; leases: boolean }) => api<Sources>('/api/platform-admin/sources', { method: 'PUT', body: d }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['feed-sources'] }); },
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const s = q.data;
  const cur = draft ?? { contrib: s.switches.contrib.on, leases: s.switches.leases.on };
  const w = s.worker;
  return (
    <div className="mb-4 space-y-4">
      <Card title="Candle sources">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Feed worker" value={w.alive ? 'running' : w.seen ? 'stopped' : 'not set up'} tone={w.alive ? 'up' : w.seen ? 'warn' : undefined}
            sub={w.alive ? `${w.pairs_live} pairs · last data ${Math.round(w.age ?? 0)} s ago` : w.seen ? `silent for ${Math.round(w.age ?? 0)} s` : 'see FEEDWORKER.md'} />
          <Stat label="Streaming now" value={s.total_streaming} sub={`${s.streaming.feed} from a feeder · ${s.streaming.lease} from ordinary accounts${s.streaming.other ? ` · ${s.streaming.other} other` : ''}`} />
          <Stat label="Stored from accounts" value={n(s.stored_from_accounts)} sub="candles shared or streamed by ordinary accounts" />
          <Stat label="Ready to switch off" value={s.ready_to_switch_off ? 'yes' : 'not yet'} tone={s.ready_to_switch_off ? 'up' : 'warn'}
            sub={s.ready_to_switch_off ? 'the worker feeds everything that streams' : 'needs a live worker and nothing streaming through accounts'} />
        </div>

        <h3 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wider text-mut">Who is feeding the market</h3>
        {s.feeders.length === 0 ? <Empty>No feeder has sent anything since the server started. Open the Quotex tab with "This browser is the price feed" ticked, or start the feed worker.</Empty> : (
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Account', 'Sender', 'State', 'Pairs live', 'Prices sent', 'Last data', 'Since'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{s.feeders.map((f) => (
              <tr key={f.uid}><td className="td font-medium" data-label="Account">{f.username}</td>
                <td className="td" data-label="Sender"><Badge tone={f.worker ? 'acc' : 'mut'}>{f.worker ? 'FEED WORKER' : 'EXTENSION'}</Badge> <span className="note">{f.agent}</span></td>
                <td className="td" data-label="State">{f.alive ? <Badge tone="up">alive</Badge> : <Badge tone="warn">silent</Badge>}</td>
                <td className="td tabular-nums" data-label="Pairs live">{f.pairs_live}</td><td className="td tabular-nums" data-label="Prices sent">{n(f.ticks)}</td>
                <td className="td note" data-label="Last data">{Math.round(f.age)} s ago</td><td className="td note" data-label="Since">{dateTime(f.since)}</td></tr>))}</tbody></table></div>)}
        <p className="note mt-2">Two feeders sending the same pair double its prices: when the feed worker runs, untick "This browser is the price feed" in your own extension.</p>
      </Card>

      <Card title="What ordinary accounts may send">
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={cur.contrib} onChange={(e) => setDraft({ ...cur, contrib: e.target.checked })} />
          <span><b>Candles from ordinary accounts.</b> Their extensions share the chart history their Quotex page loads. Off: extensions are told so and stop sending history (candles already stored stay).</span></label>
        <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={cur.leases} onChange={(e) => setDraft({ ...cur, leases: e.target.checked })} />
          <span><b>Live prices from ordinary accounts.</b> One account streams a pair when no feeder does. Off: only feeders (your tab, the feed worker) supply live prices.</span></label>
        {!s.ready_to_switch_off && !(cur.contrib && cur.leases) && <p className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-2 text-sm text-warn">The feed worker is not running yet, so with these off pairs only stream while your own Quotex tab is open on them.</p>}
        {save.isError && <div className="mt-2"><ErrorBox error={save.error} /></div>}
        <div className="mt-3 flex items-center gap-2"><button className="btn btn-primary" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>{save.isPending ? 'Saving…' : 'Save'}</button>{draft && <button className="btn" onClick={() => setDraft(null)}>Discard</button>}</div>
        <p className="note mt-2">Server defaults: <code>QXS_CONTRIB</code> {s.switches.contrib.default ? 'on' : 'off'}, <code>QXS_LIVE_FEED</code> {s.switches.leases.default ? 'on' : 'off'}. What is saved here wins and survives restarts.</p>
      </Card>
    </div>
  );
}
