import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { ago, num, pct, tfName } from '../format';
import { useStrategies } from '../hooks';
import { Badge, Card, Empty, ErrorBox, Field, FillList, PageTitle, Progress, Spinner } from '../components/ui';

interface Seg {
  trades: number; settled: number; wins: number; accuracy: number | null; ci_low: number | null; ci_high: number | null; breakeven: number | null; z: number | null; p: number | null; expectancy: number | null;
  // martingale mode (average profit per ladder, in units of the first stake)
  mean?: number | null; flat_mean?: number | null; lost_ladders?: number; lost_rate?: number | null; worst?: number | null; max_drawdown?: number | null; stake_max?: number | null; skipped?: number; open_ladders?: number;
}
interface Cand {
  name: string; rank: number; verdict: string; reason: string; fit: Seg | null; holdout: Seg | null; adj_p: number | null; trades_needed: number | null;
  pairs_above_breakeven: number | null; pairs_tested: number | null; params: Record<string, unknown>;
}
interface SweepResult {
  config: { tfs: number[] | null; holdout_fraction: number; min_trades: number; alpha: number; min_payout: number | null; pairs_used: number | null; mtg_steps?: number; mtg_mult?: number }; split_t: number; tried_now: number; tried_total: number;
  noise_max_z: number; best_holdout_z: number | null; counts: Record<string, number>; candidates: Cand[];
}
interface SweepRow { id: number; created: number; label: string; status: string; progress: number; error: string | null; n_candidates: number; counts: Record<string, number> | null; best_holdout_z: number | null; noise_max_z: number | null }
interface SweepRun extends SweepRow { result: SweepResult | null }

const VERDICT: Record<string, [string, string, string]> = {
  pass: ['up', 'PASS', 'Both periods beat break-even and the holdout result is significant even after correcting for every candidate ever tried. Paper-trade it next - this is evidence, not a promise.'],
  promising: ['acc', 'PROMISING', 'Both periods beat break-even, but luck still explains it. Keep collecting data; do not trade it yet.'],
  overfit: ['down', 'OVERFIT', 'It looked good on the older data and fell apart on the newest data - the classic sign of fitting noise.'],
  need_data: ['warn', 'NEED DATA', 'Too few trades to tell luck from skill either way.'],
  no_edge: ['mut', 'NO EDGE', 'Does not beat the break-even win rate of its payout, even on the older (fit) data.'],
  no_trades: ['mut', 'NO TRADES', 'Never signalled on the stored data.'],
  error: ['down', 'ERROR', 'Could not be evaluated.'],
};

export default function Sweeps() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const strategies = useStrategies();
  const usable = (strategies.data ?? []).filter((s) => s.status !== 'archived' && s.key !== 'consensus-3' && !s.tags.includes('meta'));
  const [picked, setPicked] = useState<number[] | null>(null);          // null = every strategy
  const [tfs, setTfs] = useState<number[]>([60]);
  const [holdout, setHoldout] = useState(0.3);
  const [minTrades, setMinTrades] = useState(1500);
  const [minPayout, setMinPayout] = useState(80);
  const [mtgSteps, setMtgSteps] = useState(0);
  const [mtgMult, setMtgMult] = useState(2.2);
  const [label, setLabel] = useState('');
  const sweeps = useQuery({
    queryKey: ['sweeps'], queryFn: () => api<SweepRow[]>('/api/sweeps'),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'running') ? 1500 : 10000),
  });
  const selected = Number(params.get('id')) || sweeps.data?.find((r) => r.status === 'done')?.id || null;
  const run = useQuery({
    queryKey: ['sweep', selected], enabled: selected !== null, queryFn: () => api<SweepRun>(`/api/sweeps/${selected}`),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  });
  const start = useMutation({
    mutationFn: () => api<SweepRun>('/api/sweeps', {
      body: { label, tfs, holdout_fraction: holdout, min_trades: minTrades, min_payout: minPayout > 0 ? minPayout : null, mtg_steps: mtgSteps, mtg_mult: mtgMult, ...(picked === null ? { all_strategies: true } : { candidates: picked.map((id) => ({ strategy_id: id })) }) },
    }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['sweeps'] }); setParams({ id: String(r.id) }); },
  });
  const del = useMutation({ mutationFn: (id: number) => api(`/api/sweeps/${id}`, { method: 'DELETE' }), onSuccess: () => { qc.invalidateQueries({ queryKey: ['sweeps'] }); setParams({}); } });
  const cancel = useMutation({ mutationFn: (id: number) => api(`/api/sweeps/${id}/cancel`, { method: 'POST' }) });

  const r = run.data?.result ?? null;
  const nPicked = picked === null ? usable.length : picked.length;

  return (
    <>
      <PageTitle title="Sweeps" sub="Score many strategies the same strict way, so luck is not mistaken for an edge. Every strategy is judged on a holdout period it was not tuned on, against the break-even win rate of its payout - not against 50%." />
      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        <Card title="New sweep">
          <div className="space-y-3">
            <div>
              <span className="label">Strategies ({nPicked} of {usable.length})</span>
              <div className="max-h-48 space-y-0.5 overflow-y-auto rounded-lg border border-line bg-panel2 p-2">
                {usable.map((s) => (
                  <label key={s.id} className="flex items-center gap-2 text-xs">
                    <input type="checkbox" checked={picked === null || picked.includes(s.id)}
                      onChange={(e) => { const cur = picked ?? usable.map((u) => u.id); setPicked(e.target.checked ? [...cur, s.id] : cur.filter((x) => x !== s.id)); }} />{s.name}</label>))}
              </div>
              <div className="mt-1 flex gap-3"><button className="note hover:text-fg" onClick={() => setPicked(null)}>all</button><button className="note hover:text-fg" onClick={() => setPicked([])}>none</button></div>
            </div>
            <div><span className="label">Timeframes</span><div className="flex gap-3">{[60, 300, 900].map((tf) => (
              <label key={tf} className="flex items-center gap-1.5"><input type="checkbox" checked={tfs.includes(tf)} onChange={(e) => setTfs(e.target.checked ? [...tfs, tf] : tfs.filter((x) => x !== tf))} />{tfName(tf)}</label>))}</div></div>
            <Field label={`Holdout: newest ${Math.round(holdout * 100)}% of the data`} hint="Judged once; never tune on it.">
              <input type="range" min={0.2} max={0.5} step={0.05} value={holdout} onChange={(e) => setHoldout(Number(e.target.value))} className="w-full" /></Field>
            <Field label="Minimum holdout trades" hint="Below this the verdict is NEED DATA. Around 1,500+ is needed before a few points of edge mean anything."><input className="input" type="number" min={50} step={100} value={minTrades} onChange={(e) => setMinTrades(Number(e.target.value))} /></Field>
            <Field label="Only pairs paying at least (%)" hint="A 60% payout needs 62.5% wins just to break even; a 90% payout needs 52.6%. 0 = every pair, however low the payout."><input className="input" type="number" min={0} max={100} step={5} value={minPayout} onChange={(e) => setMinPayout(Number(e.target.value))} /></Field>
            <Field label="Martingale (MTG)" hint="Off = judge plain trades by win rate. On = every losing trade is followed by up to N re-entries at a bigger stake, and each strategy is judged on its average profit per ladder.">
              <div className="flex items-center gap-2">
                <div className="flex overflow-hidden rounded-lg border border-line">{[0, 1, 2, 3].map((s) => (
                  <button key={s} type="button" className={`px-3 py-1 text-sm ${s === mtgSteps ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`} onClick={() => setMtgSteps(s)}>{s === 0 ? 'off' : `${s} step${s > 1 ? 's' : ''}`}</button>))}</div>
                {mtgSteps > 0 && <label className="flex items-center gap-1 text-mut">stake ×<input className="input !w-16" type="number" min={1} max={10} step={0.1} value={mtgMult} onChange={(e) => setMtgMult(Math.min(10, Math.max(1, Number(e.target.value) || 2.2)))} /></label>}
              </div></Field>
            <Field label="Label (optional)"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
            <button className="btn btn-primary w-full" disabled={!nPicked || !tfs.length || start.isPending} onClick={() => start.mutate()}>{start.isPending ? 'Starting…' : `Sweep ${nPicked} strateg${nPicked === 1 ? 'y' : 'ies'}`}</button>
            {start.isError && <ErrorBox error={start.error} />}
            <div className="note">Grids (e.g. min_agree 2 / 3 / 4) are available through the API and <code>tools/sweep.py</code>. Every distinct candidate ever swept is counted: the more different ideas you try, the higher the bar.</div>
          </div>
        </Card>

        <Card title="Sweeps" className="flex flex-col">
          {!sweeps.data ? <Spinner /> : sweeps.data.length === 0 ? <Empty>No sweeps yet. Run one on the left.</Empty> : (
            <FillList min={300}><table className="w-full">
              <thead className="sticky top-0 z-10 bg-panel"><tr>{['Sweep', 'Status', 'Candidates', 'Best holdout z', 'Pure luck reaches', 'Pass', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{sweeps.data.map((s) => (
                <tr key={s.id} className={s.id === selected ? 'bg-acc/10' : ''}>
                  <td className="td"><button className="font-semibold hover:text-acc" onClick={() => setParams({ id: String(s.id) })}>#{s.id}</button><div className="note">{s.label || ago(s.created)}</div></td>
                  <td className="td w-32">{s.status === 'running' ? <div><Badge tone="warn">RUNNING</Badge><div className="mt-1"><Progress value={s.progress} /></div>
                    <button className="note hover:text-fg" onClick={() => cancel.mutate(s.id)}>cancel</button></div> : s.status === 'done' ? <Badge tone="up">DONE</Badge> : <Badge tone="down" title={s.error ?? ''}>{s.status.toUpperCase()}</Badge>}</td>
                  <td className="td tabular-nums">{s.n_candidates}</td><td className="td tabular-nums">{num(s.best_holdout_z)}</td><td className="td tabular-nums">{num(s.noise_max_z)}</td>
                  <td className="td tabular-nums">{s.counts?.pass ?? 0}</td>
                  <td className="td"><button className="btn btn-sm btn-danger" onClick={() => del.mutate(s.id)}>✕</button></td>
                </tr>))}</tbody></table></FillList>
          )}
        </Card>
      </div>

      {selected !== null && (
        <div className="mt-4"><Card title={`Sweep #${selected} · results`}>
          {run.isLoading ? <Spinner /> : run.isError ? <ErrorBox error={run.error} /> : run.data?.status === 'running' ? <Spinner label={`Running… ${Math.round(run.data.progress * 100)}%`} /> : run.data?.status !== 'done' ? <ErrorBox error={new Error(run.data?.error ?? 'failed')} /> : r && <Results r={r} />}
        </Card></div>
      )}
    </>
  );
}

const u = (x: number | null | undefined, d = 3): string => (x === null || x === undefined ? '–' : `${x >= 0 ? '+' : ''}${x.toFixed(d)}`);

function Results({ r }: { r: SweepResult }) {
  const mtg = (r.config.mtg_steps ?? 0) > 0;
  const passes = r.counts.pass ?? 0;
  const rows = [...r.candidates].sort((a, b) => a.rank - b.rank);
  const luckZ = r.noise_max_z;
  return (
    <div className="space-y-4">
      <div className={`rounded-lg border p-3 text-sm ${passes ? 'border-up/40 bg-up/10' : 'border-line bg-panel2'}`}>
        {passes ? <><b>{passes} strateg{passes === 1 ? 'y' : 'ies'} passed.</b> Paper-trade {passes === 1 ? 'it' : 'them'} and judge on live settled signals before any real use.</>
          : <><b>Nothing has beaten luck yet.</b> The best holdout z-score is {num(r.best_holdout_z)}; with {r.tried_total} candidates tried in total, pure chance alone reaches about {num(luckZ)}. Do not trade on these signals.</>}
        <div className="note mt-1">{r.config.min_payout ? `Only the ${r.config.pairs_used} pairs paying ≥ ${r.config.min_payout}% · ` : 'All pairs, any payout · '}Holdout = newest {Math.round(r.config.holdout_fraction * 100)}% of the data · minimum {r.config.min_trades} holdout trades · significance level {r.config.alpha} after multiplying by the {r.tried_total} distinct candidates ever tried (re-running the same one does not add to it).</div>
      </div>
      {mtg && <div className="rounded-lg border p-3 text-sm" style={{ borderColor: 'rgba(56,189,248,0.35)' }}>
        <b style={{ color: '#38bdf8' }}>Martingale: up to {r.config.mtg_steps} step{r.config.mtg_steps === 1 ? '' : 's'}, stake ×{r.config.mtg_mult}.</b> After a losing trade the next candle is re-entered in the same direction. Results are in <b>units of the first stake</b>, and a strategy is judged on its <b>average profit per ladder</b> - it must be above zero in both periods. A full losing ladder costs {(1 + Array.from({ length: r.config.mtg_steps ?? 0 }, (_, k) => (r.config.mtg_mult ?? 2.2) ** (k + 1)).reduce((a, b) => a + b, 0)).toFixed(2)} units, so look at the <i>worst ladder</i> and <i>ladders lost</i> columns, not just the average. The "flat" column is the same trades without MTG.
      </div>}
      <div className="overflow-auto"><table className="w-full">
        <thead><tr>{(mtg ? ['#', 'Verdict', 'Strategy', 'Fit: avg / ladder', 'Holdout: avg / ladder', 'Flat avg (same trades)', 'Ladders lost', 'Worst ladder', 'z', 'Luck-corrected p']
          : ['#', 'Verdict', 'Strategy', 'Fit (older data)', 'Holdout (newest)', 'Break-even', 'z', 'Luck-corrected p', 'Pairs above b/e']).map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
        <tbody>{rows.map((c) => {
          const [tone, label, help] = VERDICT[c.verdict] ?? VERDICT.error;
          const adjP = c.adj_p == null ? '–' : c.adj_p < 0.001 ? '<0.001' : c.adj_p.toFixed(2);
          const plural = (n: number | undefined, w: string) => `${n ?? 0} ${w}${n === 1 ? '' : 's'}`;
          const nameCell = (
            <td className="td"><div className="font-medium">{c.name}</div><div className="note max-w-[420px] whitespace-normal normal-case tracking-normal">{c.reason}{c.trades_needed && c.verdict !== 'pass' ? ` (≈${c.trades_needed.toLocaleString()} ${mtg ? 'ladders' : 'settled trades'} would confirm it if its fit result is real)` : ''}</div></td>);
          if (mtg) {
            const f = c.fit, h = c.holdout;
            return (
              <tr key={c.rank + c.name}>
                <td className="td tabular-nums">{c.rank}</td><td className="td"><Badge tone={tone} title={help}>{label}</Badge></td>{nameCell}
                <td className="td tabular-nums">{u(f?.mean)}<div className="note">{plural(f?.settled, 'ladder')}</div></td>
                <td className="td tabular-nums">{u(h?.mean)}<div className="note">{plural(h?.settled, 'ladder')}{h?.ci_low != null ? ` · ${u(h.ci_low, 2)} to ${u(h.ci_high, 2)}` : ''}</div></td>
                <td className="td tabular-nums">{u(h?.flat_mean)}</td>
                <td className="td tabular-nums">{h?.lost_rate != null ? pct(h.lost_rate, 0) : '–'}<div className="note">{h?.lost_ladders ?? 0} of {h?.settled ?? 0}</div></td>
                <td className="td tabular-nums text-down">{h?.worst != null ? h.worst.toFixed(2) : '–'}<div className="note">drawdown {h?.max_drawdown != null ? h.max_drawdown.toFixed(0) : '–'}</div></td>
                <td className="td tabular-nums">{num(h?.z, 1)}</td><td className="td tabular-nums">{adjP}</td>
              </tr>);
          }
          return (
            <tr key={c.rank + c.name}>
              <td className="td tabular-nums">{c.rank}</td>
              <td className="td"><Badge tone={tone} title={help}>{label}</Badge></td>{nameCell}
              <td className="td tabular-nums">{pct(c.fit?.accuracy)}<div className="note">{plural(c.fit?.settled, 'trade')}</div></td>
              <td className="td tabular-nums">{pct(c.holdout?.accuracy)}<div className="note">{plural(c.holdout?.settled, 'trade')}{c.holdout?.ci_low != null ? ` · ${pct(c.holdout.ci_low, 0)}–${pct(c.holdout.ci_high, 0)}` : ''}</div></td>
              <td className="td tabular-nums">{pct(c.holdout?.breakeven ?? c.fit?.breakeven)}</td>
              <td className="td tabular-nums">{num(c.holdout?.z, 1)}</td>
              <td className="td tabular-nums">{adjP}</td>
              <td className="td tabular-nums">{c.pairs_tested ? `${c.pairs_above_breakeven}/${c.pairs_tested}` : '–'}</td>
            </tr>);
        })}</tbody></table></div>
      <div className="note whitespace-normal normal-case tracking-normal">{mtg
        ? 'Reading it: avg / ladder is the average net profit of one signal (win, or a losing trade plus its re-entries) in units of the first stake - above zero means it made money. z counts standard errors above zero; the range under the holdout average is its 95% interval. Martingale outcomes are lopsided (many small wins, rare large losses), so the interval is only trustworthy with many ladders.'
        : "Reading it: the break-even column is the win rate needed to make money at that pair's payout (100 ÷ (100 + payout)). z counts standard errors above it. The range under the holdout win rate is its 95% interval - if it contains the break-even, the result cannot be told from luck."}</div>
    </div>
  );
}
