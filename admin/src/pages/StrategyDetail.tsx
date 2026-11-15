import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, Signal, Stats, qs } from '../api';
import { ago, dateTime, pct, prettyPair, signed, tfName } from '../format';
import { Badge, Card, DirBadge, Empty, ErrorBox, PageTitle, ResultBadge, Spinner, Stat, Tabs, VerdictBadge } from '../components/ui';
import { EquityChart } from '../components/charts';
import { TrackRecord } from '../components/TrackRecord';
import { SignalHistory } from './Signals';
import { StatusSelect } from './Strategies';
import { Strategy } from '../api';

/** A group of settled signals with a one-step and a two-step martingale applied (stake x2.2 per step): accuracy and net units of the first stake. */
interface MtgLadderStat { accuracy: number | null; net: number; rescued: number; lost: number; taken: number; skipped: number }
interface MtgGroup { settled: number; wins: number; losses: number; flat_accuracy: number | null; m1: MtgLadderStat; m2: MtgLadderStat }
interface Detail extends Strategy {
  spec: Record<string, unknown>; versions: { version: number; created: number; notes: string }[];
  stats_by_tf: Record<string, Stats>; stats_by_tier: Record<string, Stats>;
  mtg?: { all: MtgGroup; by_tf: Record<string, MtgGroup>; by_tier: Record<string, MtgGroup> };
}

/** accuracy with the martingale, and what it really did to the account (net units) */
const MtgCell = ({ m, steps }: { m: MtgLadderStat | undefined; steps: 1 | 2 }) => !m || m.accuracy == null ? <span className="note">–</span> : (
  <span title={`${steps}-step martingale (stake x2.2 per step): ${m.rescued} losses rescued by a re-entry, ${m.lost} ladders lost every step${m.skipped ? `, ${m.skipped} signals skipped (fired mid-ladder)` : ''}. Net in units of the first stake.`}>
    <b className="tabular-nums">{pct(m.accuracy)}</b> <span className={`tabular-nums ${m.net >= 0 ? 'text-up' : 'text-down'}`}>{m.net >= 0 ? '+' : ''}{Math.round(m.net)}u</span></span>);

const StatRow = ({ label, s, g }: { label: string; s: Stats; g?: MtgGroup }) => (
  <tr><td className="td font-medium">{label}</td><td className="td tabular-nums">{s.total}</td><td className="td tabular-nums">{s.wins} / {s.losses}</td>
    <td className="td tabular-nums">{pct(s.accuracy)}</td><td className="td tabular-nums"><MtgCell m={g?.m1} steps={1} /></td><td className="td tabular-nums"><MtgCell m={g?.m2} steps={2} /></td>
    <td className="td"><VerdictBadge settled={s.settled} lo={s.ci_low} hi={s.ci_high} be={s.breakeven} /></td><td className="td tabular-nums">{signed(s.expectancy)}</td></tr>
);

const SIGNALS_FOR_3_POINTS = 1100;       // settled signals it takes before a 3-point edge over break-even can be told from luck (95 % range about +-3 points)

/** The honest answer to "can I trust this strategy yet?": its track record against break-even, and how far it is from being provable. */
function ProofCard({ s }: { s: Strategy }) {
  const st = s.stats;
  const above = st.ci_low != null && st.breakeven != null && st.ci_low > st.breakeven;
  const below = st.ci_high != null && st.breakeven != null && st.ci_high < st.breakeven;
  const live = s.status === 'live';
  let tone: 'up' | 'down' | 'warn' = 'warn';
  let text: string;
  if (st.settled < 30) text = `Too early to say anything: only ${st.settled} settled signals. Treat each signal as a test, not as advice.`;
  else if (above) { tone = 'up'; text = 'Its accuracy is above break-even even at the low end of its 95 % range. Keep watching: the check repeats as signals arrive, and results fade as often as they last.'; }
  else if (below) { tone = 'down'; text = `Its accuracy is below break-even even at the high end of its 95 % range: on this record it loses money.${live ? ' A live strategy is muted automatically once its last 100 signals are clearly below break-even.' : ''}`; }
  else text = `Not proven. With ${st.settled} settled signals the range still contains break-even, so luck and a real edge cannot be told apart; a 3-point edge needs about ${SIGNALS_FOR_3_POINTS.toLocaleString()} signals.`;
  const show = live && !above;
  return (
    <Card title="Is it proven yet?">
      <TrackRecord stats={st} note={false} />
      <div className={`mt-2 text-sm ${tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : 'text-fg'}`}>{text}</div>
      {st.settled >= 30 && !above && !below && (
        <div className="mt-2"><div className="mb-1 flex justify-between text-xs text-mut"><span>Signals toward a provable verdict</span><span className="tabular-nums">{st.settled.toLocaleString()} / {SIGNALS_FOR_3_POINTS.toLocaleString()}</span></div>
          <div className="h-1.5 overflow-hidden rounded bg-line"><div className="h-full bg-acc" style={{ width: `${Math.min(100, (st.settled / SIGNALS_FOR_3_POINTS) * 100)}%` }} /></div></div>)}
      {show && <div className="mt-2 rounded-lg border border-warn/40 bg-warn/10 p-2 text-sm text-warn">This strategy is <b>live</b> (it sends alerts) without having shown an edge. Consider switching it to <b>Paper</b> (top right) and promoting it only when its range clears break-even.</div>}
    </Card>
  );
}

export default function StrategyDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState('overview');
  const [version, setVersion] = useState<number | null>(null);
  const d = useQuery({ queryKey: ['strategy', id], queryFn: () => api<Detail>(`/api/strategies/${id}`), refetchInterval: 8000 });
  const eq = useQuery({ queryKey: ['equity-strategy', id], queryFn: () => api<{ n: number; points: [number, number][] }>(`/api/equity?strategy_id=${id}`), refetchInterval: 15000 });
  const sigs = useQuery({ queryKey: ['strategy-signals', id], enabled: tab === 'signals', queryFn: () => api<{ total: number; rows: Signal[] }>(`/api/signals${qs({ strategy_id: id, limit: 30 })}`) });
  const ver = useQuery({ queryKey: ['strategy-version', id, version], enabled: version !== null, queryFn: () => api<{ spec: unknown; notes: string; created: number }>(`/api/strategies/${id}/versions/${version}`) });
  const del = useMutation({ mutationFn: () => api<{ result: string }>(`/api/strategies/${id}`, { method: 'DELETE' }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); nav('/strategies'); } });

  if (d.isError) return <ErrorBox error={d.error} />;
  if (!d.data) return <Spinner />;
  const s = d.data;
  const spec = version !== null && ver.data ? ver.data.spec : s.spec;

  return (
    <>
      <PageTitle title={s.name} sub={<span>{s.description}</span>}
        right={<>
          <StatusSelect s={s} />
          <Link className="btn" to={`/backtests?strategy=${s.id}`}>Backtest</Link>
          <Link className="btn" to={`/clinic?strategy=${s.id}`}>Clinic</Link>
          <Link className="btn" to={`/strategies/${s.id}/clone`}>Clone</Link>
          {s.kind === 'custom' && <Link className="btn btn-primary" to={`/strategies/${s.id}/edit`}>Edit (new version)</Link>}
          {s.kind === 'custom' && <button className="btn btn-danger" onClick={() => confirm('Delete this strategy? If it already has signals it will be archived instead.') && del.mutate()}>Delete</button>}
        </>} />
      <div className="-mt-3 mb-4 flex flex-wrap items-center gap-2">
        <Badge tone={s.kind === 'builtin' ? 'mut' : 'acc'}>{s.kind}</Badge><Badge>v{s.version}</Badge>{s.tags.map((t) => <Badge key={t}>{t}</Badge>)}
        {s.error && <Badge tone="down" title={s.error.join('; ')}>spec error</Badge>}
        <span className="note">{s.running ? `${(s.evaluations ?? 0).toLocaleString()} evaluations since backend start` : 'not running (draft, paused or archived) - switch it to Paper or Live to start recording'} · updated {ago(s.updated)}</span>
      </div>
      {s.status === 'live' && s.muted && <div className="mb-4 rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">Alerts are muted automatically: this strategy's last 100 settled signals are clearly below break-even. It keeps recording as paper.</div>}
      {del.isError && <ErrorBox error={del.error} />}

      <Tabs tabs={[{ key: 'overview', label: 'Overview' }, { key: 'spec', label: 'Spec & versions' }, { key: 'signals', label: 'Recent signals' }]} value={tab} onChange={setTab} />

      {tab === 'overview' && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
            <Stat label="Signals" value={s.stats.total} sub={`${s.stats.pending} pending`} />
            <Stat label="Accuracy" value={pct(s.stats.accuracy)} sub={`95% ${pct(s.stats.ci_low)}–${pct(s.stats.ci_high)}`} />
            {([1, 2] as const).map((k) => { const m = s.mtg?.all[k === 1 ? 'm1' : 'm2']; return (
              <Stat key={k} label={`With MTG-${k}`} value={pct(m?.accuracy)}
                sub={m ? <span title={`${k}-step martingale (stake x2.2 per step) on this strategy's real settled signals. The percentage counts a signal as won if the first trade or a re-entry won; the net is what it did to the account, in units of the first stake.`}>
                  net <b className={m.net >= 0 ? 'text-up' : 'text-down'}>{m.net >= 0 ? '+' : ''}{Math.round(m.net)} u</b> · {m.rescued} rescued</span> : ''} />); })}
            <Stat label="Break-even" value={pct(s.stats.breakeven)} />
            <Stat label="Expectancy" value={signed(s.stats.expectancy)} tone={s.stats.expectancy == null ? undefined : s.stats.expectancy >= 0 ? 'up' : 'down'} sub="units per trade" />
            <Stat label="Verdict" value={<VerdictBadge settled={s.stats.settled} lo={s.stats.ci_low} hi={s.stats.ci_high} be={s.stats.breakeven} />} sub={`${s.stats.settled} settled`} />
          </div>
          <ProofCard s={s} />
          <Card title="Equity curve (settled signals)">{eq.data ? <EquityChart points={eq.data.points} /> : <Spinner />}</Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="By timeframe"><div className="overflow-x-auto"><table className="w-full"><thead><tr>{['TF', 'Signals', 'W / L', 'Acc.', 'MTG-1', 'MTG-2', 'Verdict', 'Exp.'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{Object.entries(s.stats_by_tf).map(([tf, st]) => <StatRow key={tf} label={tfName(Number(tf))} s={st} g={s.mtg?.by_tf[tf]} />)}</tbody></table></div></Card>
            <Card title="By tier"><div className="overflow-x-auto"><table className="w-full"><thead><tr>{['Tier', 'Signals', 'W / L', 'Acc.', 'MTG-1', 'MTG-2', 'Verdict', 'Exp.'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{Object.entries(s.stats_by_tier).map(([t, st]) => <StatRow key={t} label={`Tier ${t}`} s={st} g={s.mtg?.by_tier[t]} />)}</tbody></table></div></Card>
          </div>
          <SignalHistory strategyId={s.id} />
        </div>
      )}

      {tab === 'spec' && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title={`Spec ${version !== null ? `(version ${version})` : `(current, v${s.version})`}`} className="lg:col-span-2">
            <pre className="max-h-[560px] overflow-auto rounded-lg bg-panel2 p-3 font-mono text-xs">{JSON.stringify(spec, null, 2)}</pre>
          </Card>
          <Card title="Versions">
            <div className="space-y-1.5">
              {s.versions.map((v) => (
                <button key={v.version} onClick={() => setVersion(v.version === s.version ? null : v.version)}
                  className={`block w-full rounded-lg border p-2 text-left text-sm ${(version ?? s.version) === v.version ? 'border-acc bg-acc/10' : 'border-line bg-panel2'}`}>
                  <b>v{v.version}</b> <span className="note">{dateTime(v.created)}</span><div className="note">{v.notes}</div>
                </button>))}
            </div>
            <p className="note mt-3">Versions are immutable. A change creates a new version; signals always record which version produced them.</p>
          </Card>
        </div>
      )}

      {tab === 'signals' && (
        <Card title="Recent signals" right={sigs.data && `${sigs.data.total} total`}>
          {!sigs.data ? <Spinner /> : sigs.data.rows.length === 0 ? <Empty>No signals yet.</Empty> : (
            <table className="w-full"><thead><tr>{['Candle', 'Pair', 'TF', 'Signal', 'Tier', 'Str.', 'Mode', 'Result'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{sigs.data.rows.map((r) => (
                <tr key={r.id}><td className="td tabular-nums">{dateTime(r.target_t)}</td><td className="td">{prettyPair(r.symbol)}</td><td className="td">{tfName(r.tf)}</td>
                  <td className="td"><DirBadge dir={r.direction} /></td><td className="td">{r.tier}</td><td className="td">{r.strength}</td><td className="td">{r.mode}</td><td className="td"><ResultBadge status={r.status} /></td></tr>))}</tbody></table>
          )}
          <div className="mt-3"><Link className="text-acc hover:underline" to="/signals">Open full history →</Link></div>
        </Card>
      )}
    </>
  );
}
