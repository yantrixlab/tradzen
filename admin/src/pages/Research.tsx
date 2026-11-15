import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, dateTime, pct } from '../format';
import { ResearchModel, ResearchRun, useResearchList, useResearchRun, useResearchStatus } from '../hooks';
import { Badge, Card, Empty, ErrorBox, PageTitle, Progress, Spinner, Stat } from '../components/ui';

const VERDICT: Record<string, { tone: string; label: string; text: string }> = {
  no_signal: { tone: 'mut', label: 'NO SIGNAL', text: 'The models found nothing in the last few candles that carries over to new data.' },
  weak: { tone: 'warn', label: 'WEAK', text: 'A real but tiny effect - far too small to trade on.' },
  signal: { tone: 'up', label: 'SIGNAL', text: 'A model cleared every gate. Investigate it with a sweep before trusting it.' },
  need_data: { tone: 'warn', label: 'NEED DATA', text: 'Not enough unseen candles yet to tell skill from luck.' },
  control_failed: { tone: 'down', label: 'CONTROL FAILED', text: 'The self-check failed, so this run cannot be trusted.' },
};

/** AUC on a 0.46-0.60 scale: the chance band around 0.50 is shaded, the dashed line is the minimum worth acting on, the dot is the result (with its 95% range). */
function AucBar({ m, minAuc }: { m: ResearchModel; minAuc: number }) {
  const lo = 0.46, hi = 0.6, W = 190, x = (v: number) => ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * W;
  return (
    <svg width={W} height={22} className="block" role="img" aria-label={`AUC ${m.auc_test.toFixed(3)}`}>
      <rect x={0} y={9} width={W} height={4} rx={2} fill="#1e2747" />
      <rect x={x(0.5 - m.band)} y={5} width={x(0.5 + m.band) - x(0.5 - m.band)} height={12} rx={2} fill="#8a93b2" opacity={0.28} />
      <line x1={x(0.5)} x2={x(0.5)} y1={3} y2={19} stroke="#8a93b2" />
      <line x1={x(minAuc)} x2={x(minAuc)} y1={3} y2={19} stroke="#22c55e" strokeDasharray="3 2" />
      <line x1={x(m.ci_low)} x2={x(m.ci_high)} y1={11} y2={11} stroke="#c4b5fd" strokeWidth={2} />
      <circle cx={x(m.auc_test)} cy={11} r={4.5} fill={m.verdict === 'signal' ? '#22c55e' : '#c4b5fd'} stroke="#0b1126" />
    </svg>
  );
}

export default function Research() {
  const qc = useQueryClient();
  const status = useResearchStatus();
  const list = useResearchList();
  const [picked, setPicked] = useState<number | null>(null);
  const latestDone = list.data?.find((r) => r.status === 'done')?.id ?? null;
  const selected = picked ?? status.data?.running_id ?? latestDone ?? list.data?.[0]?.id ?? null;
  const run = useResearchRun(selected);
  const start = useMutation({
    mutationFn: () => api<ResearchRun>('/api/research/run', { body: {} }),
    onSuccess: (r) => { setPicked(r.id); qc.invalidateQueries({ queryKey: ['research'] }); qc.invalidateQueries({ queryKey: ['research-status'] }); },
  });
  const s = status.data;
  const r = run.data?.result ?? null;
  const v = r ? VERDICT[r.verdict] ?? VERDICT.no_signal : null;

  return (
    <>
      <PageTitle title="Research" sub="Every night this asks one question: can any model, shown only the last few closed candles, predict whether the next candle closes up - on data it has never seen? It is built to say 'no' plainly, and to refuse to be fooled by a lucky result." />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <Card title="Schedule" className="flex flex-col">
          <div className="space-y-3 text-sm">
            {!s ? <Spinner /> : <>
              <div>{s.hour_utc === null ? <b>Nightly run is switched off</b> : <>Runs every night at <b>{String(s.hour_utc).padStart(2, '0')}:00 UTC</b> ({new Date(Date.UTC(2000, 0, 1, s.hour_utc)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} your time)</>}</div>
              <div className="note">{s.hour_utc === null ? 'Set QXS_RESEARCH_HOUR (0-23) before starting the backend to turn it on.' : 'The backend must be running at that time. If it was off, the run is made up as soon as it starts again.'}</div>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Last run" value={s.last_run ? ago(s.last_run) : 'never'} />
                <Stat label="Next run" value={s.running ? 'now' : s.next_run ? dateTime(s.next_run).split(', ').pop() ?? '–' : '–'} sub={s.next_run && !s.running ? dateTime(s.next_run).split(', ')[0] : undefined} />
              </div>
              {s.running && <div><Badge tone="warn">RUNNING</Badge><div className="mt-1"><Progress value={run.data?.progress ?? 0} /></div></div>}
              <button className="btn btn-primary w-full" disabled={s.running || start.isPending} onClick={() => start.mutate()}>{s.running ? 'Running…' : start.isPending ? 'Starting…' : 'Run now'}</button>
              {start.isError && <ErrorBox error={start.error} />}
              <div className="note">Takes a few minutes on a laptop and uses one CPU core. Your signals and charts keep working meanwhile.</div>
            </>}
          </div>
        </Card>

        <Card title={run.data ? `Check #${run.data.id} · ${run.data.source} · ${ago(run.data.created)}` : 'Latest check'}>
          {!selected ? <Empty>No check has run yet. It will run tonight, or press Run now.</Empty> : run.isLoading ? <Spinner /> : run.isError ? <ErrorBox error={run.error} />
            : run.data?.status === 'running' ? <Spinner label={`Running… ${Math.round((run.data.progress ?? 0) * 100)}%`} />
            : run.data?.status === 'error' ? <ErrorBox error={new Error(run.data.error ?? 'failed')} />
            : r && v && (
              <div className="space-y-4">
                <div className={`rounded-lg border p-3 ${r.verdict === 'signal' ? 'border-up/40 bg-up/10' : r.verdict === 'control_failed' ? 'border-down/40 bg-down/10' : 'border-line bg-panel2'}`}>
                  <div className="flex items-center gap-2"><Badge tone={v.tone}>{v.label}</Badge><span className="font-semibold">{r.summary}</span></div>
                  <div className="note mt-1 whitespace-normal normal-case tracking-normal">{v.text}</div>
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <Stat label="Candles used" value={r.data.rows.toLocaleString()} sub={`${r.data.pairs} pairs`} />
                  <Stat label="Unseen test candles" value={r.data.test.toLocaleString()} sub={`newest ${Math.round((r.data.test / r.data.rows) * 100)}% of the data`} />
                  <Stat label="Self-check" value={r.control.ok ? 'passed' : 'FAILED'} sub={`shuffled labels scored ${r.control.auc_test.toFixed(3)} (should be ~0.5)`} tone={r.control.ok ? 'up' : 'down'} />
                  <Stat label="Sentiment test" value={r.sentiment ? (r.sentiment.edge_found ? 'edge found' : 'no edge yet') : '–'} sub={r.sentiment ? `${r.sentiment.matched.toLocaleString()} matched candles` : undefined} tone={r.sentiment?.edge_found ? 'up' : undefined} />
                </div>
                <div className="overflow-auto"><table className="w-full">
                  <thead><tr>{['Sees', 'Model', 'AUC on data it learned from', 'AUC on UNSEEN data', 'Corrected p', 'Top 10% most confident', 'Verdict'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                  <tbody>{r.models.map((m) => (
                    <tr key={`${m.window}-${m.model}`}>
                      <td className="td tabular-nums">last {m.window}</td><td className="td">{m.model}</td>
                      <td className="td tabular-nums text-mut">{m.auc_train.toFixed(3)}</td>
                      <td className="td"><div className="tabular-nums font-semibold">{m.auc_test.toFixed(3)}</div><AucBar m={m} minAuc={r.config.min_auc} /></td>
                      <td className="td tabular-nums">{m.p_adj >= 0.995 ? '1.00' : m.p_adj < 0.001 ? '<0.001' : m.p_adj.toFixed(2)}</td>
                      <td className="td tabular-nums">{m.top10.win !== null ? pct(m.top10.win) : '–'}<div className="note">{m.top10.n.toLocaleString()} calls vs {pct(r.config.breakeven, 0)} break-even</div></td>
                      <td className="td"><Badge tone={(VERDICT[m.verdict] ?? VERDICT.no_signal).tone} title={m.reason}>{(VERDICT[m.verdict] ?? VERDICT.no_signal).label}</Badge></td>
                    </tr>))}</tbody></table></div>
                <div className="note whitespace-normal normal-case tracking-normal">
                  <b>How to read the bars:</b> the shaded band is where pure luck lands for this many candles (0.500 ± {r.models[0]?.band.toFixed(3)}); the green dashed line (AUC {r.config.min_auc}) is the minimum worth acting on; the dot is the result with its 95% range.
                  A model that scores well on the data it learned from but ~0.50 on unseen data has only memorised noise - that gap is the whole story. A result only counts when it is significant after correcting for the {r.tried} models tried, clears {r.config.min_auc}, and has at least {r.config.min_test.toLocaleString()} unseen candles.
                </div>
              </div>)}
        </Card>
      </div>

      <div className="mt-4"><Card title="History">
        {!list.data ? <Spinner /> : list.data.length === 0 ? <Empty>No runs yet.</Empty> : (
          <div className="max-h-[320px] overflow-auto"><table className="w-full">
            <thead className="sticky top-0 z-10 bg-panel"><tr>{['Run', 'When', 'Source', 'Verdict', 'Best AUC on unseen data', 'Candles', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{list.data.map((x) => (
              <tr key={x.id} className={x.id === selected ? 'bg-acc/10' : ''}>
                <td className="td"><button className="font-semibold hover:text-acc" onClick={() => setPicked(x.id)}>#{x.id}</button></td>
                <td className="td note">{dateTime(x.created)}</td><td className="td note">{x.source}</td>
                <td className="td">{x.status === 'running' ? <Badge tone="warn">RUNNING</Badge> : x.status === 'error' ? <Badge tone="down" title={x.error ?? ''}>ERROR</Badge> : <Badge tone={(VERDICT[x.verdict ?? ''] ?? VERDICT.no_signal).tone}>{(VERDICT[x.verdict ?? ''] ?? VERDICT.no_signal).label}</Badge>}</td>
                <td className="td tabular-nums">{x.best_auc !== null ? x.best_auc.toFixed(3) : '–'}</td>
                <td className="td tabular-nums note">{x.rows ? x.rows.toLocaleString() : '–'}</td>
                <td className="td note max-w-[420px] truncate" title={x.summary ?? ''}>{x.summary}</td>
              </tr>))}</tbody></table></div>)}
      </Card></div>
    </>
  );
}
