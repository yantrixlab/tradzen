import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { ago, pct, prettyPair, tfName, time } from '../format';
import { useStrategies } from '../hooks';
import { Badge, Card, DirBadge, Empty, PageTitle, Spinner, StatusBadge } from '../components/ui';
import { StatusSelect } from './Strategies';

interface AlertEvent { seq: number; kind: string; t: number; name: string; symbol: string; tf: number; direction: string; tier: string; strength: number; status: string; strategy: string; also: { name: string }[]; payout: number | null }

export default function Alerts() {
  const strategies = useStrategies();
  const events = useQuery({ queryKey: ['alert-events'], queryFn: () => api<{ seq: number; events: AlertEvent[] }>('/api/events?after=0'), refetchInterval: 5000 });
  if (!strategies.data) return <Spinner />;
  const live = strategies.data.filter((s) => s.status === 'live');
  const sigEvents = (events.data?.events ?? []).filter((e) => e.kind === 'signal').reverse();

  return (
    <>
      <PageTitle title="Alerts" sub="Which strategies can notify you. Alerts reach the Quotex page as a card with sound, and as a desktop notification when the tab is not in front." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Alerting strategies" className="lg:col-span-2" right={<span className="note">{live.length} live</span>}>
          {live.length === 0 ? <Empty>No strategy is live - nothing will alert. Promote a paper strategy once it has earned it.</Empty> : (
            <table className="w-full"><thead><tr>{['Strategy', 'Mode', 'Settled', 'Accuracy', 'Alert state'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{live.map((s) => (
                <tr key={s.id}><td className="td font-medium"><Link className="hover:text-acc" to={`/strategies/${s.id}`}>{s.name}</Link></td><td className="td"><StatusSelect s={s} /></td>
                  <td className="td tabular-nums">{s.stats.settled}</td><td className="td tabular-nums">{pct(s.stats.accuracy)}</td>
                  <td className="td">{s.muted ? <Badge tone="down">AUTO-MUTED</Badge> : <Badge tone="up">ACTIVE</Badge>}</td></tr>))}</tbody></table>
          )}
          <div className="mt-4 rounded-lg bg-panel2 p-3 text-sm text-mut">
            <b className="text-fg">How alerting works</b>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              <li>Only <StatusBadge status="live" /> strategies alert; <StatusBadge status="paper" /> ones are recorded silently.</li>
              <li>Several strategies agreeing on the same pair, timeframe and direction produce <b>one</b> card listing the others.</li>
              <li>A live strategy is <b>auto-muted</b> when its last 100 settled signals have a 95 % upper bound below break-even; it keeps recording and un-mutes by itself if it recovers.</li>
              <li>Sound, card position, tier filter and desktop notifications are set per browser in the extension popup (icon → Alerts).</li>
            </ul>
          </div>
        </Card>
        <Card title="Recent alerts">
          {!events.data ? <Spinner /> : sigEvents.length === 0 ? <Empty>No alerts since the backend started.</Empty> : (
            <div className="space-y-2">{sigEvents.slice(0, 12).map((e) => (
              <div key={e.seq} className="rounded-lg bg-panel2 p-2 text-sm">
                <div className="flex items-center justify-between"><span><DirBadge dir={e.direction} /> <b>{prettyPair(e.symbol)}</b> <span className="note">{tfName(e.tf)}</span></span><span className="note">{time(e.t)} · {ago(e.t)}</span></div>
                <div className="note">{e.strategy} · Tier {e.tier} · strength {e.strength}{e.also.length ? ` · +${e.also.length} agreeing` : ''}</div>
              </div>))}</div>
          )}
        </Card>
      </div>
    </>
  );
}
