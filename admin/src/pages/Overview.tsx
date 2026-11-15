import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, Stats } from '../api';
import { ago, pct, prettyPair, signed, tfName, time } from '../format';
import { strategyName, useLiveState, useStrategies } from '../hooks';
import { Card, DirBadge, Empty, PageTitle, ResultBadge, Spinner, Stat, StatusBadge, Tabs, VerdictBadge } from '../components/ui';
import { EquityChart } from '../components/charts';

export default function Overview() {
  const live = useLiveState();
  const strategies = useStrategies();
  const [mode, setMode] = useState<'live' | 'all'>('live');
  const q = mode === 'live' ? '?mode=live' : '';
  const stats = useQuery({ queryKey: ['stats', mode], queryFn: () => api<Stats>(`/api/stats${q}`), refetchInterval: 10000 });
  const equity = useQuery({ queryKey: ['equity', mode], queryFn: () => api<{ n: number; profit: number; points: [number, number][] }>(`/api/equity${q}`), refetchInterval: 15000 });

  if (!live.data || !strategies.data) return <Spinner />;
  const s = stats.data;
  const now = live.data.now;
  const streaming = live.data.symbols.filter((x) => x.streaming);
  const age = live.data.ingest.last_recv ? now - live.data.ingest.last_recv : null;
  const board = [...strategies.data].filter((x) => x.stats.total > 0).sort((a, b) => b.stats.settled - a.stats.settled).slice(0, 8);
  const issued = live.data.recent.filter((r) => r.direction !== 'SKIP').slice(0, 12);

  return (
    <>
      <PageTitle title="Overview" sub="Live feed health, measured results and what is running right now."
        right={<Tabs tabs={[{ key: 'live', label: 'Live strategies' }, { key: 'all', label: 'All (incl. paper)' }]} value={mode} onChange={(k) => setMode(k as 'live' | 'all')} />} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Feed" value={age !== null && age < 10 ? 'LIVE' : age === null ? 'WAITING' : 'STALE'} tone={age !== null && age < 10 ? 'up' : 'warn'}
          sub={`${streaming.length} pairs · ${live.data.ingest.ticks.toLocaleString()} ticks`} />
        <Stat label="Signals issued" value={s?.total ?? '–'} sub={`${s?.calls ?? 0} ▲ / ${s?.puts ?? 0} ▼ · ${s?.pending ?? 0} pending`} />
        <Stat label="Win accuracy" value={pct(s?.accuracy)} sub={s?.settled ? `${s.wins}W / ${s.losses}L · 95% ${pct(s.ci_low)}–${pct(s.ci_high)}` : 'no settled signals'} />
        <Stat label="Break-even" value={pct(s?.breakeven)} sub="at the average payout" />
        <Stat label="Expectancy / trade" value={signed(s?.expectancy)} tone={s?.expectancy == null ? undefined : s.expectancy >= 0 ? 'up' : 'down'} sub="units of stake" />
      </div>
      {s && <div className="mt-2"><VerdictBadge settled={s.settled} lo={s.ci_low} hi={s.ci_high} be={s.breakeven} /> <span className="note ml-2">judge only after a few hundred settled signals</span></div>}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title={`Equity curve (${mode === 'live' ? 'live strategies' : 'all strategies'})`} right={equity.data && `${equity.data.n} settled`} className="lg:col-span-2">
          {equity.data ? <EquityChart points={equity.data.points} /> : <Spinner />}
        </Card>
        <Card title="Streaming now" right={<Link className="text-acc hover:underline" to="/data">data →</Link>}>
          {streaming.length ? (
            <div className="space-y-1.5">
              {streaming.map((x) => (
                <div key={x.symbol} className="flex items-center justify-between rounded-lg bg-panel2 px-3 py-1.5">
                  <span className="font-medium">{prettyPair(x.symbol)}</span>
                  <span className="tabular-nums text-mut">{x.last_price ?? '–'}</span>
                </div>
              ))}
            </div>
          ) : <Empty>No pair is streaming. Open a chart on Quotex with the extension enabled.</Empty>}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Strategy leaderboard" right={<Link className="text-acc hover:underline" to="/strategies">all strategies →</Link>}>
          {board.length ? (
            <table className="w-full"><thead><tr><th className="th">Strategy</th><th className="th">Mode</th><th className="th">Settled</th><th className="th">Accuracy</th><th className="th">Verdict</th></tr></thead>
              <tbody>{board.map((x) => (
                <tr key={x.id}>
                  <td className="td"><Link className="font-medium hover:text-acc" to={`/strategies/${x.id}`}>{x.name}</Link></td>
                  <td className="td"><StatusBadge status={x.status} /></td><td className="td tabular-nums">{x.stats.settled}</td>
                  <td className="td tabular-nums">{pct(x.stats.accuracy)}</td>
                  <td className="td"><VerdictBadge settled={x.stats.settled} lo={x.stats.ci_low} hi={x.stats.ci_high} be={x.stats.breakeven} /></td>
                </tr>))}</tbody></table>
          ) : <Empty>No strategy has issued signals yet.</Empty>}
        </Card>
        <Card title="Latest signals" right={<Link className="text-acc hover:underline" to="/signals">history →</Link>}>
          {issued.length ? (
            <table className="w-full"><thead><tr><th className="th">Candle</th><th className="th">Pair</th><th className="th">Strategy</th><th className="th">Signal</th><th className="th">Result</th></tr></thead>
              <tbody>{issued.map((r) => (
                <tr key={r.id}><td className="td tabular-nums">{time(r.target_t)}</td><td className="td">{prettyPair(r.symbol)} <span className="note">{tfName(r.tf)}</span></td>
                  <td className="td note">{strategyName(strategies.data, r.strategy_id)}{r.mode === 'paper' && ' · paper'}</td><td className="td"><DirBadge dir={r.direction} /></td>
                  <td className="td"><ResultBadge status={r.status} /></td></tr>))}</tbody></table>
          ) : <Empty>No signals yet.</Empty>}
        </Card>
      </div>

      <Card title="Feed log" className="mt-4" right={<span className="note">why data paused, socket events, auto-mutes</span>}>
        {live.data.feed_events.length ? (
          <div className="grid gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-2">
            {live.data.feed_events.slice(0, 12).map((e, i) => (
              <div key={i} className="truncate"><span className="text-mut">{ago(e.t, now).padEnd(8)}</span> <span className="text-fg">{e.e}</span> <span className="text-mut">{e.d}</span></div>
            ))}
          </div>
        ) : <Empty>No events.</Empty>}
      </Card>
    </>
  );
}
