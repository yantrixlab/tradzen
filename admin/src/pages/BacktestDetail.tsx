import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { dateTime, num, pct, prettyPair, signed, tfName } from '../format';
import { Badge, Card, ErrorBox, PageTitle, Progress, ResultBadge, Spinner, Stat, VerdictBadge } from '../components/ui';
import { EquityChart } from '../components/charts';
import { BacktestCharts } from '../components/BacktestCharts';

interface Summary {
  trades: number; wins: number; losses: number; draws: number; settled: number; accuracy: number | null; ci_low: number | null; ci_high: number | null; breakeven: number | null;
  expectancy: number | null; profit: number; profit_factor: number | null; max_drawdown: number; longest_losing_streak: number; longest_winning_streak: number; equity: [number, number][];
}
interface Run {
  id: number; label: string; strategy: string | null; strategy_id: number | null; status: string; progress: number; error: string | null; n_trades: number;
  config: { symbols: string[] | null; tfs: number[] | null; valid_fraction: number };
  result: { summary: Summary; train?: Summary; valid?: Summary; split_t: number | null; by_tf: Record<string, Summary>; by_tier: Record<string, Summary>; by_direction: Record<string, Summary>; by_symbol: Record<string, Summary> } | null;
}
interface TradeRow { symbol: string; tf: number; t: number; dir: number; tier: string; strength: number; result: string; profit: number; entry: number; exit: number }

function GroupTable({ title, data, label }: { title: string; data: Record<string, Summary>; label: (k: string) => string }) {
  return (
    <Card title={title}>
      <table className="w-full"><thead><tr>{['', 'Trades', 'Accuracy', 'Verdict', 'Exp.', 'Max DD'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
        <tbody>{Object.entries(data).map(([k, s]) => (
          <tr key={k}><td className="td font-medium">{label(k)}</td><td className="td tabular-nums">{s.trades}</td><td className="td tabular-nums">{pct(s.accuracy)}</td>
            <td className="td"><VerdictBadge {...{ settled: s.settled, lo: s.ci_low, hi: s.ci_high, be: s.breakeven }} /></td><td className="td tabular-nums">{signed(s.expectancy)}</td><td className="td tabular-nums">{num(s.max_drawdown, 1)}</td></tr>))}</tbody></table>
    </Card>
  );
}

export default function BacktestDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const run = useQuery({ queryKey: ['backtest', id], queryFn: () => api<Run>(`/api/backtests/${id}`), refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false) });
  const trades = useQuery({ queryKey: ['bt-trades', id, offset], enabled: run.data?.status === 'done', queryFn: () => api<{ total: number; rows: TradeRow[] }>(`/api/backtests/${id}/trades?limit=25&offset=${offset}`) });
  const del = useMutation({ mutationFn: () => api(`/api/backtests/${id}`, { method: 'DELETE' }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['backtests'] }); nav('/backtests'); } });

  if (run.isError) return <ErrorBox error={run.error} />;
  if (!run.data) return <Spinner />;
  const r = run.data;
  const res = r.result;
  const s = res?.summary;

  return (
    <>
      <PageTitle title={`Backtest #${r.id}${r.label ? ` · ${r.label}` : ''}`} sub={<span>{r.strategy ?? 'ad-hoc spec'} · {(r.config.tfs ?? []).map(tfName).join(', ') || 'all timeframes'} · {r.config.symbols ? `${r.config.symbols.length} pairs` : 'all pairs'}</span>}
        right={<><Link className="btn" to="/backtests">← Runs</Link>{r.status === 'done' && <Link className="btn btn-primary" to={`/clinic?run=${r.id}`}>Analyse losses in the clinic</Link>}
          <button className="btn btn-danger" onClick={() => confirm('Delete this run?') && del.mutate()}>Delete</button></>} />
      {r.status === 'running' && <Card><Badge tone="warn">RUNNING</Badge><div className="mt-2"><Progress value={r.progress} /></div></Card>}
      {(r.status === 'error' || r.status === 'cancelled') && <ErrorBox error={r.error ?? r.status} />}
      {s && res && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label="Trades" value={s.trades} sub={`${s.wins}W / ${s.losses}L / ${s.draws}D`} />
            <Stat label="Accuracy" value={pct(s.accuracy)} sub={`95% ${pct(s.ci_low)}–${pct(s.ci_high)}`} />
            <Stat label="Break-even" value={pct(s.breakeven)} />
            <Stat label="Expectancy" value={signed(s.expectancy)} tone={s.expectancy == null ? undefined : s.expectancy >= 0 ? 'up' : 'down'} sub="units / trade" />
            <Stat label="Profit factor" value={num(s.profit_factor, 2)} sub={`total ${signed(s.profit, 1)} units`} />
            <Stat label="Max drawdown" value={num(s.max_drawdown, 1)} sub={`longest losing run ${s.longest_losing_streak}`} />
          </div>
          <div><VerdictBadge settled={s.settled} lo={s.ci_low} hi={s.ci_high} be={s.breakeven} /> <span className="note ml-2">on the whole sample; see the validation period below for the honest check</span></div>
          <Card title="Equity curve (cumulative profit, units of stake)"><EquityChart points={s.equity} marker={res.split_t} /></Card>
          {res.train && res.valid && (
            <Card title="Fit period vs validation period" right={<span className="note">validation = newest {Math.round(r.config.valid_fraction * 100)}% (from {dateTime(res.split_t)})</span>}>
              <table className="w-full"><thead><tr>{['Period', 'Trades', 'Accuracy', '95% interval', 'Break-even', 'Expectancy', 'Verdict'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{([['Fit (older)', res.train], ['Validation (newest)', res.valid]] as [string, Summary][]).map(([name, x]) => (
                  <tr key={name}><td className="td font-medium">{name}</td><td className="td tabular-nums">{x.trades}</td><td className="td tabular-nums">{pct(x.accuracy)}</td>
                    <td className="td tabular-nums">{pct(x.ci_low)}–{pct(x.ci_high)}</td><td className="td tabular-nums">{pct(x.breakeven)}</td><td className="td tabular-nums">{signed(x.expectancy)}</td>
                    <td className="td"><VerdictBadge {...{ settled: x.settled, lo: x.ci_low, hi: x.ci_high, be: x.breakeven }} /></td></tr>))}</tbody></table>
              <p className="note mt-2">If the validation period is much worse than the fit period, the result is probably luck or a regime that has passed.</p>
            </Card>
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <GroupTable title="By timeframe" data={res.by_tf} label={(k) => tfName(Number(k))} />
            <GroupTable title="By tier" data={res.by_tier} label={(k) => `Tier ${k}`} />
            <GroupTable title="By direction" data={res.by_direction} label={(k) => k} />
            <GroupTable title="By pair (largest samples)" data={res.by_symbol} label={prettyPair} />
          </div>
          <Card title="Trades on the chart" right={<span className="note normal-case tracking-normal">the exact trades this run recorded; the shaded area is the validation period</span>}>
            <BacktestCharts symbols={r.config.symbols ?? []} tfs={(r.config.tfs?.length ? r.config.tfs : [60, 300, 900]).slice().sort((a, b) => a - b)} strategyId={r.strategy_id} runId={r.id} />
          </Card>
          <Card title="Trades (newest first)" right={trades.data && `${trades.data.total} total`}>
            {!trades.data ? <Spinner /> : (
              <>
                <table className="w-full"><thead><tr>{['Candle', 'Pair', 'TF', 'Signal', 'Tier', 'Str.', 'Entry → exit', 'Result', 'Profit'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                  <tbody>{trades.data.rows.map((t, i) => (
                    <tr key={i}><td className="td tabular-nums">{dateTime(t.t)}</td><td className="td">{prettyPair(t.symbol)}</td><td className="td">{tfName(t.tf)}</td>
                      <td className="td">{t.dir > 0 ? <b className="text-up">▲ CALL</b> : <b className="text-down">▼ PUT</b>}</td><td className="td">{t.tier}</td><td className="td">{t.strength}</td>
                      <td className="td tabular-nums note">{num(t.entry, 5)} → {num(t.exit, 5)}</td><td className="td"><ResultBadge status={t.result} /></td><td className="td tabular-nums">{signed(t.profit, 2)}</td></tr>))}</tbody></table>
                <div className="mt-3 flex justify-between"><span className="note">{offset + 1}–{Math.min(offset + 25, trades.data.total)} of {trades.data.total}</span>
                  <div className="flex gap-2"><button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 25))}>← Newer</button>
                    <button className="btn btn-sm" disabled={offset + 25 >= trades.data.total} onClick={() => setOffset(offset + 25)}>Older →</button></div></div>
              </>)}
          </Card>
        </div>
      )}
    </>
  );
}
