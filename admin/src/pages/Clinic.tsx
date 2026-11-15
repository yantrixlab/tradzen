import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { ago, pct, signed } from '../format';
import { useStrategies } from '../hooks';
import { Badge, Card, Empty, ErrorBox, Field, Modal, PageTitle, Spinner, Stat } from '../components/ui';
import { AccuracyBars } from '../components/charts';
import type { RunRow } from './Backtests';

interface Cell { n: number; wins: number; losses: number; acc: number | null; ci_low: number | null; ci_high: number | null; expectancy: number | null }
interface Suggestion { filter: Record<string, unknown>; text: string }
interface Finding {
  dimension: string; label: string; bucket: string; direction: 'worse' | 'better'; n: number; wins: number; acc: number; ci_low: number; ci_high: number; baseline: number;
  lift: number; p: number; q: number; significant: boolean; validation: { n: number; acc: number | null; baseline: number | null; holds: boolean | null }; suggestion: Suggestion | null;
}
interface Analysis {
  n_trades: number; n_decided: number; tests: number; min_n: number; q: number; split_t: number | null; strategy_id: number | null; source: string; run_id: number | null;
  baseline: { fit: Cell; valid: Cell; all: Cell } | null;
  dimensions: { key: string; label: string; buckets: { bucket: string; all: Cell; fit: Cell; valid: Cell }[] }[]; findings: Finding[];
}
interface Compare { base: Seg; candidate: Seg }
interface Seg { trades: number; settled: number; accuracy: number | null; expectancy: number | null; profit: number; max_drawdown: number }
interface TryResult { comparison: { fit: Compare; valid: Compare | null; verdict: string; reason: string }; suggestion: Suggestion }
interface Insight { id: number; created: number; strategy: string | null; dimension: string; bucket: string; n: number; acc: number; baseline: number; q: number; status: string; suggestion: Suggestion | null; validation: { holds: boolean | null } | null }

const verdictTone = (v: string) => (v === 'holds' ? 'up' : v === 'noise' ? 'down' : v === 'weak' ? 'warn' : 'mut');

export default function Clinic() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const strategies = useStrategies();
  const runs = useQuery({ queryKey: ['backtests'], queryFn: () => api<RunRow[]>('/api/backtests') });
  const [source, setSource] = useState<'live' | 'backtest'>(params.get('run') ? 'backtest' : 'live');
  const [strategyId, setStrategyId] = useState(params.get('strategy') ?? '');
  const [runId, setRunId] = useState(params.get('run') ?? '');
  const [vf, setVf] = useState(0.3);
  const [minN, setMinN] = useState(30);
  const [dim, setDim] = useState('adx');
  const [trying, setTrying] = useState<Finding | null>(null);

  const analyze = useMutation({
    mutationFn: (save: boolean) => api<Analysis>('/api/clinic/analyze', { body: { source, run_id: source === 'backtest' ? Number(runId) : null, strategy_id: source === 'live' && strategyId ? Number(strategyId) : null, valid_fraction: vf, min_n: minN, save } }),
    onSuccess: (_d, save) => { if (save) qc.invalidateQueries({ queryKey: ['insights'] }); },
  });
  const insights = useQuery({ queryKey: ['insights'], queryFn: () => api<Insight[]>('/api/insights') });
  const setStatus = useMutation({ mutationFn: (v: { id: number; status: string }) => api(`/api/insights/${v.id}`, { method: 'PATCH', body: { status: v.status } }), onSuccess: () => qc.invalidateQueries({ queryKey: ['insights'] }) });

  const a = analyze.data;
  const dimension = a?.dimensions.find((d) => d.key === dim) ?? a?.dimensions[0];
  const sid = a?.strategy_id ?? (strategyId ? Number(strategyId) : null);
  const doneRuns = (runs.data ?? []).filter((r) => r.status === 'done' && r.n_trades > 0);

  return (
    <>
      <PageTitle title="Clinic" sub="In which situations does a strategy lose - and does a fix hold up on newer data? Findings are fitted on the older part of the trades, corrected for the many buckets tested, and re-checked on the newest part." />
      <Card className="mb-4">
        <div className="grid items-end gap-3 md:grid-cols-5">
          <Field label="Analyse"><select className="input" value={source} onChange={(e) => setSource(e.target.value as 'live' | 'backtest')}><option value="live">Live / paper signals</option><option value="backtest">A backtest run</option></select></Field>
          {source === 'live' ? (
            <Field label="Strategy"><select className="input" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}><option value="">All strategies</option>
              {strategies.data?.filter((s) => s.stats.settled > 0).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.stats.settled})</option>)}</select></Field>
          ) : (
            <Field label="Backtest run"><select className="input" value={runId} onChange={(e) => setRunId(e.target.value)}><option value="">Select…</option>
              {doneRuns.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.strategy ?? 'ad-hoc'} · {r.n_trades} trades</option>)}</select></Field>
          )}
          <Field label={`Validation: newest ${Math.round(vf * 100)}%`}><input type="range" min={0.1} max={0.5} step={0.05} value={vf} onChange={(e) => setVf(Number(e.target.value))} className="w-full" /></Field>
          <Field label="Min trades per bucket"><input className="input" type="number" min={10} value={minN} onChange={(e) => setMinN(Number(e.target.value))} /></Field>
          <button className="btn btn-primary" disabled={analyze.isPending || (source === 'backtest' && !runId)} onClick={() => analyze.mutate(false)}>{analyze.isPending ? 'Analysing…' : 'Run analysis'}</button>
        </div>
        {analyze.isError && <div className="mt-3"><ErrorBox error={analyze.error} /></div>}
      </Card>

      {a && a.baseline && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Decided trades" value={a.n_decided} sub={`${a.n_trades} incl. draws`} />
            <Stat label="Fit baseline" value={pct(a.baseline.fit.acc)} sub={`${a.baseline.fit.n} trades (older)`} />
            <Stat label="Validation baseline" value={pct(a.baseline.valid.acc)} sub={`${a.baseline.valid.n} trades (newest)`} />
            <Stat label="Buckets tested" value={a.tests} sub={`≥ ${a.min_n} trades each`} />
            <Stat label="Significant" value={a.findings.filter((f) => f.significant).length} tone={a.findings.some((f) => f.significant) ? 'warn' : undefined} sub={`false-discovery rate ${(a.q * 100).toFixed(0)}%`} />
          </div>

          <Card title="Findings" right={<button className="btn btn-sm" onClick={() => analyze.mutate(true)}>Save significant findings</button>}>
            {a.findings.filter((f) => f.significant).length === 0 ? (
              <Empty>No bucket is statistically distinguishable from the average yet. That is the usual result with a small sample - keep collecting data. (Differences that look big below are normal noise.)</Empty>
            ) : (
              <div className="space-y-2">{a.findings.filter((f) => f.significant).map((f, i) => <FindingCard key={i} f={f} onTry={() => setTrying(f)} canTry={!!sid && !!f.suggestion} />)}</div>
            )}
          </Card>

          <Card title="Explore any situation" right={<select className="input !w-64" value={dimension?.key} onChange={(e) => setDim(e.target.value)}>{a.dimensions.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}</select>}>
            {dimension && <AccuracyBars baseline={a.baseline.all.acc} breakeven={null}
              rows={dimension.buckets.map((b) => ({ label: b.bucket, n: b.all.n, acc: b.all.acc, lo: b.all.ci_low, hi: b.all.ci_high }))} />}
            {dimension && (
              <table className="mt-4 w-full"><thead><tr>{['Bucket', 'All n', 'Accuracy', 'Fit n', 'Fit acc.', 'Valid n', 'Valid acc.', 'Exp.'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{dimension.buckets.map((b) => (
                  <tr key={b.bucket}><td className="td font-medium">{b.bucket}</td><td className="td tabular-nums">{b.all.n}</td><td className="td tabular-nums">{pct(b.all.acc)}</td>
                    <td className="td tabular-nums">{b.fit.n}</td><td className="td tabular-nums">{pct(b.fit.acc)}</td><td className="td tabular-nums">{b.valid.n}</td><td className="td tabular-nums">{pct(b.valid.acc)}</td>
                    <td className="td tabular-nums">{signed(b.all.expectancy)}</td></tr>))}</tbody></table>)}
          </Card>
        </div>
      )}
      {a && !a.baseline && <Card><Empty>No settled trades to analyse for this selection yet.</Empty></Card>}
      {!a && !analyze.isPending && <Card><Empty>Pick a source and run the analysis. Live signals need a few hundred settled trades to say anything; a backtest on stored candles can give you that sooner.</Empty></Card>}
      {analyze.isPending && <Spinner label="Analysing…" />}

      <Card title="Saved insights" className="mt-4" right={<span className="note">significant findings you saved</span>}>
        {!insights.data ? <Spinner /> : insights.data.length === 0 ? <Empty>Nothing saved yet.</Empty> : (
          <table className="w-full"><thead><tr>{['When', 'Strategy', 'Situation', 'Accuracy', 'q', 'Holds on new data', 'Suggestion', 'Status'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{insights.data.map((x) => (
              <tr key={x.id}><td className="td note">{ago(x.created)}</td><td className="td">{x.strategy ?? 'all'}</td><td className="td"><b>{x.dimension}</b> {x.bucket}</td>
                <td className="td tabular-nums">{pct(x.acc)} <span className="note">vs {pct(x.baseline)}</span></td><td className="td tabular-nums">{x.q.toFixed(4)}</td>
                <td className="td">{x.validation?.holds === true ? <Badge tone="up">yes</Badge> : x.validation?.holds === false ? <Badge tone="down">no</Badge> : <Badge>n/a</Badge>}</td>
                <td className="td note">{x.suggestion?.text ?? '–'}</td>
                <td className="td"><select className="input !w-auto !py-1 !text-xs" value={x.status} onChange={(e) => setStatus.mutate({ id: x.id, status: e.target.value })}>{['new', 'tested', 'applied', 'dismissed'].map((s) => <option key={s}>{s}</option>)}</select></td></tr>))}</tbody></table>
        )}
      </Card>

      {trying && sid && <TryModal finding={trying} strategyId={sid} vf={vf} onClose={() => setTrying(null)} onApplied={(id) => nav(`/strategies/${id}`)} />}
    </>
  );
}

function FindingCard({ f, onTry, canTry }: { f: Finding; onTry: () => void; canTry: boolean }) {
  const h = f.validation.holds;
  return (
    <div className={`rounded-lg border p-3 ${f.direction === 'worse' ? 'border-down/30 bg-down/5' : 'border-up/30 bg-up/5'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><span className="font-semibold">{f.label}</span> <Badge tone={f.direction === 'worse' ? 'down' : 'up'}>{f.bucket}</Badge></div>
        <div className="flex items-center gap-2 text-xs tabular-nums">
          <span>{pct(f.acc)} <span className="text-mut">vs {pct(f.baseline)} average</span></span><span className="text-mut">n={f.n}</span><span className="text-mut">q={f.q.toFixed(4)}</span>
          <Badge tone={h === true ? 'up' : h === false ? 'down' : 'mut'} title={`validation: ${pct(f.validation.acc)} on ${f.validation.n} newer trades vs ${pct(f.validation.baseline)} average`}>
            {h === true ? 'HOLDS ON NEW DATA' : h === false ? 'DID NOT HOLD ON NEW DATA' : 'NOT ENOUGH NEW DATA'}</Badge>
        </div>
      </div>
      {f.suggestion ? (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm">Suggestion: <b>{f.suggestion.text}</b></div>
          <button className="btn btn-sm btn-primary" disabled={!canTry} onClick={onTry}>Try this filter</button>
        </div>
      ) : <div className="note mt-2">Informational: cannot be expressed as a single filter.</div>}
    </div>
  );
}

function TryModal({ finding, strategyId, vf, onClose, onApplied }: { finding: Finding; strategyId: number; vf: number; onClose: () => void; onApplied: (id: number) => void }) {
  const qc = useQueryClient();
  const sugg = finding.suggestion!;
  const t = useQuery({ queryKey: ['try', strategyId, JSON.stringify(sugg), vf], queryFn: () => api<TryResult>('/api/clinic/try', { body: { strategy_id: strategyId, suggestion: sugg, valid_fraction: vf } }), retry: 0 });
  const apply = useMutation({ mutationFn: () => api<{ id: number }>('/api/clinic/apply', { body: { strategy_id: strategyId, suggestion: sugg } }), onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['strategies'] }); onApplied(r.id); } });
  const c = t.data?.comparison;
  const Row = ({ label, a, b }: { label: string; a: Seg; b: Seg }) => (
    <tr><td className="td font-medium">{label}</td>
      <td className="td tabular-nums">{a.trades} <span className="note">→</span> {b.trades}</td><td className="td tabular-nums">{pct(a.accuracy)} <span className="note">→</span> <b>{pct(b.accuracy)}</b></td>
      <td className="td tabular-nums">{signed(a.expectancy)} <span className="note">→</span> <b>{signed(b.expectancy)}</b></td><td className="td tabular-nums">{signed(a.profit, 1)} <span className="note">→</span> {signed(b.profit, 1)}</td></tr>
  );
  return (
    <Modal open onClose={onClose} title={`Try: ${sugg.text}`} width={760}>
      {t.isLoading ? <Spinner label="Running baseline and filtered backtests…" /> : t.isError ? <ErrorBox error={t.error} /> : c && (
        <div className="space-y-3">
          <div className="rounded-lg border border-line bg-panel2 p-3"><Badge tone={verdictTone(c.verdict)}>{c.verdict.toUpperCase().replace('_', ' ')}</Badge><span className="ml-2 text-sm">{c.reason}</span></div>
          <table className="w-full"><thead><tr>{['Period', 'Trades (before → after)', 'Accuracy', 'Expectancy / trade', 'Profit (units)'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody><Row label="Fit (older data)" a={c.fit.base} b={c.fit.candidate} />{c.valid && <Row label="Validation (newest)" a={c.valid.base} b={c.valid.candidate} />}</tbody></table>
          <p className="note">The filter was suggested from the fit period, so only the validation row is an honest test. Re-running on the same data cannot confirm it - it needs new data.</p>
          <div className="flex justify-end gap-2">
            <button className="btn" onClick={onClose}>Close</button>
            <button className="btn btn-primary" disabled={apply.isPending} onClick={() => apply.mutate()}>Apply as a new paper strategy</button>
          </div>
          {apply.isError && <ErrorBox error={apply.error} />}
          <div className="note">Applying creates a <b>new</b> strategy (original + this filter) in paper mode so it can prove itself on live data. The original is never changed. <Link className="text-acc hover:underline" to="/strategies" onClick={onClose}>Strategies →</Link></div>
        </div>
      )}
    </Modal>
  );
}
