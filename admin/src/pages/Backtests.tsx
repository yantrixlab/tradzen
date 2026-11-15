import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { ago, pct, signed, tfName } from '../format';
import { useCoverage, useLiveState, useMe, useStrategies } from '../hooks';
import { Badge, Card, Empty, ErrorBox, Field, FillList, PageTitle, Progress, Spinner } from '../components/ui';
import { BacktestCharts } from '../components/BacktestCharts';
import { PairPicker } from '../components/PairPicker';

/** One-step martingale numbers of a run (units = the first stake). `rescued` = trades the first leg lost but the next candle won. */
export interface RunMtg {
  steps: number; mult: number; signals: number; taken: number; skipped: number; wins: number; rescued: number; lost: number; draws: number; open: number;
  accuracy: number | null; net: number; flat_net: number; error?: string;
}
export interface RunRow {
  id: number; created: number; finished: number | null; label: string; strategy_id: number | null; strategy: string | null; status: string; progress: number; error: string | null;
  n_trades: number; accuracy: number | null; expectancy: number | null; breakeven: number | null; config: { symbols: string[] | null; tfs: number[] | null; valid_fraction: number };
  mtg1?: RunMtg | null; mtg1_pending?: boolean;
}

/** The two martingale columns of a run row: accuracy with a one-step re-entry (plus what it really did to the account), and how many trades the re-entry won. */
function MtgCells({ r }: { r: RunRow }) {
  const m = r.mtg1;
  if (r.status !== 'done') return <><td className="td note">–</td><td className="td note">–</td></>;
  if (!m) return <><td className="td note" title="Calculating for this older run…">…</td><td className="td note">…</td></>;
  if (m.error || !m.taken) return <><td className="td note">–</td><td className="td note">–</td></>;
  const worse = m.net < m.flat_net;
  const rate = m.rescued + m.lost > 0 ? m.rescued / (m.rescued + m.lost) : null;
  const tip = `${m.taken.toLocaleString()} signals taken${m.skipped ? ` (${m.skipped.toLocaleString()} fired mid-ladder and were skipped)` : ''}: ${m.wins.toLocaleString()} won outright, ${m.rescued.toLocaleString()} rescued by the re-entry, ${m.lost.toLocaleString()} lost both. Net with MTG ${m.net.toFixed(1)} units vs ${m.flat_net.toFixed(1)} flat (stake ×${m.mult}).`;
  return (
    <>
      <td className="td tabular-nums" title={tip}>{pct(m.accuracy)}
        <div className={`note ${m.net >= 0 ? 'text-up' : 'text-down'}`}>net {m.net >= 0 ? '+' : ''}{Math.round(m.net).toLocaleString()} u</div>
        <div className="note" title="The same trades without martingale (every trade the same stake)">flat {m.flat_net >= 0 ? '+' : ''}{Math.round(m.flat_net).toLocaleString()} u{worse ? ' · MTG worse' : ''}</div></td>
      <td className="td tabular-nums" title={tip}>{m.rescued.toLocaleString()}
        <div className="note">{rate !== null ? `${pct(rate, 0)} of losses` : '–'}</div></td>
    </>
  );
}

export default function Backtests() {
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const strategies = useStrategies();
  const coverage = useCoverage();
  const live = useLiveState();
  const isAdmin = useMe().data?.user.role === 'admin';
  const [strategyId, setStrategyId] = useState(params.get('strategy') ?? '');
  const [tfs, setTfs] = useState<number[]>([60]);
  const [picked, setPicked] = useState<string[]>([]);
  const [vf, setVf] = useState(0.3);
  const [label, setLabel] = useState('');
  const runs = useQuery({
    queryKey: ['backtests'], queryFn: () => api<RunRow[]>('/api/backtests'),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'running') ? 1500 : q.state.data?.some((r) => r.mtg1_pending) ? 3000 : 10000),
  });
  const start = useMutation({
    mutationFn: () => api<RunRow>('/api/backtests', { body: { strategy_id: Number(strategyId), tfs, symbols: picked.length ? picked : null, valid_fraction: vf, label } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/backtests/${id}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }) });
  const clearAll = useMutation({
    mutationFn: () => api<{ runs: number; trades: number }>('/api/backtests?confirm=all', { method: 'DELETE' }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['backtests'] }); qc.invalidateQueries({ queryKey: ['clinic'] }); },
  });
  const askClear = () => {
    const n = runs.data?.length ?? 0;
    if (window.confirm(`Delete ALL ${n} backtest run${n === 1 ? '' : 's'} and every trade stored with them?\n\nThis cannot be undone. Your candles, strategies and live / paper signals are NOT touched.`)) clearAll.mutate();
  };

  const candles = (coverage.data ?? []).filter((c) => tfs.includes(c.tf) && (!picked.length || picked.includes(c.symbol))).reduce((a, c) => a + c.candles, 0);
  const usable = (strategies.data ?? []).filter((s) => s.status !== 'archived' && s.key !== 'consensus-3' && !s.tags.includes('meta'));

  return (
    <>
      <PageTitle title="Backtest lab" sub="Replay stored candles through a strategy with exactly the same rules as live. A trade enters at the next candle's open and settles at its close; the newest part of the data is held back as a validation period." />
      <div className="grid gap-4 lg:grid-cols-[440px_1fr]">
        <Card title="New backtest">
          <div className="space-y-3">
            <Field label="Strategy"><select className="input" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              <option value="">Select…</option>{usable.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <div><span className="label">Timeframes</span><div className="flex gap-3">{[60, 300, 900].map((tf) => (
              <label key={tf} className="flex items-center gap-1.5"><input type="checkbox" checked={tfs.includes(tf)} onChange={(e) => setTfs(e.target.checked ? [...tfs, tf] : tfs.filter((x) => x !== tf))} />{tfName(tf)}</label>))}</div></div>
            <PairPicker coverage={coverage.data} live={live.data?.symbols} tfs={tfs} picked={picked} onChange={setPicked} />
            <Field label={`Validation period: newest ${Math.round(vf * 100)}% of trades`}><input type="range" min={0.1} max={0.5} step={0.05} value={vf} onChange={(e) => setVf(Number(e.target.value))} className="w-full" /></Field>
            <Field label="Label (optional)"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
            <div className="note">≈ {candles.toLocaleString()} stored candles will be replayed.</div>
            <button className="btn btn-primary w-full" disabled={!strategyId || !tfs.length || start.isPending} onClick={() => start.mutate()}>{start.isPending ? 'Starting…' : 'Run backtest'}</button>
            {start.isError && <ErrorBox error={start.error} />}
          </div>
        </Card>

        <Card title="Runs" className="flex flex-col"
          right={isAdmin && runs.data && runs.data.length > 0 ? <button className="btn border-down/50 text-down hover:bg-down/10" disabled={clearAll.isPending} onClick={askClear}
            title="Delete every backtest run and its trades (candles, strategies and signals are kept)">{clearAll.isPending ? 'Clearing…' : '🗑 Clear all backtest data'}</button> : undefined}>
          {clearAll.isError && <ErrorBox error={clearAll.error} />}
          {clearAll.isSuccess && <div className="note mb-2">Cleared {clearAll.data.runs} run{clearAll.data.runs === 1 ? '' : 's'} and {clearAll.data.trades.toLocaleString()} stored trades.</div>}
          {!runs.data ? <Spinner /> : runs.data.length === 0 ? <Empty>No runs yet. Pick a strategy and run one.</Empty> : (
            <FillList><table className="w-full text-[13px] [&_.td]:px-1.5 [&_.th]:px-1.5">
              <thead className="sticky top-0 z-10 bg-panel"><tr>{['Run', 'Strategy', 'Scope', 'Status', 'Trades', 'Accuracy', 'With MTG-1', 'Won by MTG-1', 'Break-even', 'Exp.', ''].map((h) => (
                <th key={h} className="th" title={h === 'With MTG-1' ? 'Win rate if every losing trade is followed by ONE re-entry on the next candle (stake ×2.2). Read it together with the net result under it: a higher win rate can still lose more money.'
                  : h === 'Won by MTG-1' ? 'Trades the first leg lost but the re-entry won. The percentage is how often a loss was rescued.' : undefined}>{h}</th>))}</tr></thead>
              <tbody>{runs.data.map((r) => (
                <tr key={r.id}>
                  <td className="td"><Link className="font-semibold hover:text-acc" to={`/backtests/${r.id}`}>#{r.id}</Link><div className="note">{r.label || ago(r.created)}</div></td>
                  <td className="td">{r.strategy ?? 'ad-hoc spec'}</td>
                  <td className="td note">{(r.config.tfs ?? []).map(tfName).join(', ') || 'all tf'} · {r.config.symbols ? `${r.config.symbols.length} pairs` : 'all pairs'}</td>
                  <td className="td w-32">{r.status === 'running' ? <div><Badge tone="warn">RUNNING</Badge><div className="mt-1"><Progress value={r.progress} /></div></div>
                    : r.status === 'done' ? <Badge tone="up">DONE</Badge> : <Badge tone="down" title={r.error ?? ''}>{r.status.toUpperCase()}</Badge>}</td>
                  <td className="td tabular-nums">{r.n_trades}</td><td className="td tabular-nums">{pct(r.accuracy)}</td>
                  <MtgCells r={r} />
                  <td className="td tabular-nums">{pct(r.breakeven)}</td>
                  <td className="td tabular-nums">{signed(r.expectancy)}</td>
                  <td className="td"><button className="btn btn-sm btn-danger" onClick={() => del.mutate(r.id)}>✕</button></td>
                </tr>))}</tbody></table></FillList>
          )}
        </Card>
      </div>

      <div className="mt-4">
        <Card title="Charts · what the strategy would have traded" right={<span className="note normal-case tracking-normal">{strategyId ? 'BUY ▲ / SELL ▼ markers are replayed live from the stored candles - no run needed' : 'pick a strategy on the left to draw its buy/sell signals on these charts'}</span>}>
          <BacktestCharts symbols={picked} tfs={tfs.length ? [...tfs].sort((a, b) => a - b) : [60]} strategyId={strategyId ? Number(strategyId) : null} />
        </Card>
      </div>
    </>
  );
}
