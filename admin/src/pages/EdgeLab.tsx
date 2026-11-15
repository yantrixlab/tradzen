import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, pct } from '../format';
import { Badge, Card, Empty, ErrorBox, PageTitle, Progress, Spinner, Stat } from '../components/ui';

interface Seg { n: number; win: number | null; lo: number | null; hi: number | null; be: number }
interface Battery {
  verdict: 'PASS' | 'PROMISING' | 'FAIL'; why?: string; checks: { name: string; ok: boolean; text: string }[]; overall: Seg; older?: Seg; newest?: Seg;
  ideas_counted: number; z: number; noise_band?: [number, number];
}
interface Cand {
  rank: number; atoms: string[]; side: 'follow' | 'fade'; rule: string; best_effort: boolean;
  discovery: { n: number; win: number; z: number; be: number }; confirm: { n: number; win: number | null; z: number; p: number; be: number; ok: boolean }; battery: Battery;
}
interface Redesign { strategy_id: number; name: string; status: string; improved: boolean; base: Battery; redesigned: Battery }
interface Run {
  id: string; started: number; finished?: number; candles: number; applied?: boolean; applied_ids?: number[];
  global: { verdict: string; why: string; auc?: number; lo?: number; hi?: number; n_test?: number; rows?: number } | null;
  search: { ok: boolean; rows: number; pairs?: number; slices?: { discovery: number; confirm: number; final: number }; alpha?: number; why?: string };
  funnel?: { candidates: number; rules_scored: number; kept_after_discovery: number; confirmed: number };
  candidates: Cand[]; rejected?: { rule: string; side: string; discovery: { win: number; n: number }; confirm: { win: number | null; n: number } }[];
  outcome: { status: 'edge_found' | 'possible_edge' | 'no_edge' | 'no_data'; best?: number; why: string };
  redesign: Redesign[];
}
interface RunRow { id: string; started: number; outcome: string | null; why: string | null; funnel: Run['funnel'] | null; global: string | null; applied: boolean }
interface Status { job: { running: boolean; stage: string; progress: number; error: string | null }; runs: RunRow[]; latest: Run | null; candles_1m: number; pairs_1m: number; weekly: boolean; auto: boolean }

const OUT = {
  edge_found: { tone: 'up', label: 'EDGE FOUND', text: 'A rule passed every check on candles the search never saw.' },
  possible_edge: { tone: 'warn', label: 'POSSIBLE EDGE', text: 'Above break-even on the newest data but not proven.' },
  no_edge: { tone: 'mut', label: 'NO EDGE FOUND', text: 'Nothing built from the old data beat break-even on unseen candles.' },
  no_data: { tone: 'warn', label: 'NOT ENOUGH DATA', text: '' },
} as const;
const BV = { PASS: 'up', PROMISING: 'warn', FAIL: 'down' } as const;

function Funnel({ r }: { r: Run }) {
  const f = r.funnel;
  if (!f) return null;
  const steps: [string, number, string][] = [
    ['Rules built from conditions', f.candidates, '1–3 conditions each, follow or fade'], ['Kept after discovery', f.kept_after_discovery, 'best of the oldest 50% of candles'],
    ['Confirmed', f.confirmed, `next 20%, corrected test (α=${r.search.alpha ? r.search.alpha.toFixed(4) : '–'})`], ['Passed the full battery', r.candidates.filter((c) => c.battery.verdict === 'PASS').length, 'newest 30%, real backtest'],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {steps.map(([l, n, s]) => <Stat key={l} label={l} value={n.toLocaleString()} sub={s} tone={l === 'Passed the full battery' && n > 0 ? 'up' : undefined} />)}
    </div>
  );
}

function Checks({ b }: { b: Battery }) {
  return <ul className="mt-1 space-y-0.5 text-sm">{b.checks.map((c) => <li key={c.name}><span className={c.ok ? 'text-up' : 'text-down'}>{c.ok ? '✓' : '✗'}</span> <b>{c.name}</b> <span className="note">{c.text}</span></li>)}</ul>;
}

export default function EdgeLab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['edgelab'], queryFn: () => api<Status>('/api/edgelab'), refetchInterval: (s) => (s.state.data?.job.running ? 2000 : 15000) });
  const start = useMutation({ mutationFn: () => api('/api/edgelab/run', { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['edgelab'] }) });
  const done = () => { qc.invalidateQueries({ queryKey: ['edgelab'] }); qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['strategy-health'] }); };
  const apply = useMutation({ mutationFn: (v: { id: string; force: boolean }) => api(`/api/edgelab/runs/${v.id}/apply?force=${v.force}`, { body: {} }), onSuccess: done });
  const [open, setOpen] = useState<number | null>(null);
  const s = q.data;
  const r = s?.latest ?? null;
  const o = r ? OUT[r.outcome.status] : null;
  return (
    <>
      <PageTitle title="Edge Lab" sub="The AI searches your stored candles for an edge, and acts on what it finds. 1) Is anything predictive at all? 2) Build strategies from the old data and test them on candles they never saw. 3) If a real edge appears: create it as a paper strategy and redesign the existing strategies with it. If there is no edge it says so and changes nothing." />
      {!s ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : (
        <div className="space-y-4">
          <Card title="Run the search">
            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <div className="space-y-2 text-sm">
                <div><b>{s.candles_1m.toLocaleString()}</b> one-minute candles from <b>{s.pairs_1m}</b> pairs are stored. The search uses the oldest 50% to find rules, the next 20% to confirm them, and keeps the newest 30% for a final test only the survivors see.</div>
                <div className="note">Runs in a separate low-priority process (a minute or two) so live signals are never slowed. {s.weekly ? 'It also runs by itself every week.' : 'Weekly runs are off (QXS_EDGELAB_WEEKLY).'} {s.auto ? 'If it finds an edge it creates the paper strategies automatically.' : 'Automatic creation is off (QXS_EDGELAB_AUTO).'}</div>
              </div>
              <div className="space-y-2">
                <button className="btn btn-primary w-full" disabled={s.job.running || start.isPending} onClick={() => start.mutate()}>{s.job.running ? 'Searching…' : 'Run edge search'}</button>
                {s.job.running && <div><Progress value={s.job.progress} /><div className="note mt-1">{s.job.stage}</div></div>}
                {s.job.error && <div className="note text-down">{s.job.error}</div>}
              </div>
            </div>
          </Card>

          {!r ? <Empty>No search has run yet. Press Run edge search (the first run also starts by itself after a few minutes).</Empty> : (
            <>
              <div className={`rounded-2xl border p-5 ${r.outcome.status === 'edge_found' ? 'border-up/50 bg-up/10' : r.outcome.status === 'possible_edge' ? 'border-warn/50 bg-warn/10' : 'border-line bg-panel'}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="note">Latest search · {ago(r.started)} · {r.candles.toLocaleString()} candles</div>
                    <div className="mt-1 flex items-center gap-3"><span className="text-3xl font-black">{o?.label}</span><Badge tone={o?.tone ?? 'mut'}>{r.outcome.status}</Badge></div>
                    <div className="mt-1 text-sm">{r.outcome.why}</div>
                  </div>
                  <div className="space-y-2 text-right">
                    {r.applied ? <Badge tone="up">paper strategies created ({r.applied_ids?.length})</Badge> :
                      r.outcome.status === 'no_edge' ? <button className="btn" disabled={apply.isPending} onClick={() => window.confirm('No edge was found. Install the best rule as a PAPER strategy anyway, just to measure it on live signals?') && apply.mutate({ id: r.id, force: true })}>Install best rule on paper anyway</button> :
                        (r.outcome.status === 'edge_found' || r.outcome.status === 'possible_edge') ? <button className="btn btn-primary" disabled={apply.isPending} onClick={() => apply.mutate({ id: r.id, force: false })}>Create paper strategies{r.redesign.some((x) => x.improved) ? ' + redesigns' : ''}</button> : null}
                    {apply.isError && <ErrorBox error={apply.error} />}
                  </div>
                </div>
              </div>

              <Card title="Stage 1 · Is anything predictive at all?">
                {!r.global ? <div className="note">Skipped.</div> : (
                  <div className="text-sm">
                    <Badge tone={r.global.verdict === 'proven' ? 'up' : r.global.verdict === 'weak' ? 'warn' : 'mut'}>{r.global.verdict.replace('_', ' ').toUpperCase()}</Badge> <span className="ml-2">{r.global.why}</span>
                    <div className="note mt-1">A walk-forward model (always scored on later candles, with a shuffled-label control) tried to predict the next candle from 56 features. AUC 0.500 is a coin flip.</div>
                  </div>
                )}
              </Card>

              <Card title="Stage 2 · Strategies built from the old data">
                {!r.search.ok ? <div className="note">{r.search.why}</div> : <>
                  <Funnel r={r} />
                  <div className="note mt-2">{r.search.slices ? `Discovery ${r.search.slices.discovery.toLocaleString()} · confirmation ${r.search.slices.confirm.toLocaleString()} · final (unseen) ${r.search.slices.final.toLocaleString()} trades-worth of candles. ` : ''}The 95% range of every final test is widened for the number of ideas tried in the last 90 days (see the ledger).</div>
                  <table className="mt-3 w-full text-sm">
                    <thead><tr className="text-left text-mut"><th className="py-1">Rule the AI built</th><th>Discovery</th><th>Confirmation</th><th>Unseen (newest 30%)</th><th>Verdict</th></tr></thead>
                    <tbody>{r.candidates.map((c) => (
                      <>
                        <tr key={c.rank} className="cursor-pointer border-t border-line align-top hover:bg-panel2" onClick={() => setOpen(open === c.rank ? null : c.rank)}>
                          <td className="py-1.5 pr-2">{c.rule}{c.best_effort && <div className="note">best-effort: nothing survived confirmation, so the top rule was built and tested anyway</div>}</td>
                          <td className="tabular-nums">{pct(c.discovery.win, 1)} <span className="note">of {c.discovery.n.toLocaleString()}</span></td>
                          <td className="tabular-nums">{c.confirm.win == null ? '–' : pct(c.confirm.win, 1)} <span className="note">of {c.confirm.n.toLocaleString()}</span>{c.confirm.ok ? <div className="text-up text-xs">confirmed</div> : <div className="note">not confirmed</div>}</td>
                          <td className="tabular-nums">{c.battery.newest ? <>{pct(c.battery.newest.win, 1)} <span className="note">of {c.battery.newest.n.toLocaleString()} · BE {pct(c.battery.newest.be, 1)}</span></> : '–'}</td>
                          <td><Badge tone={BV[c.battery.verdict]}>{c.battery.verdict}</Badge></td>
                        </tr>
                        {open === c.rank && <tr key={`${c.rank}d`}><td colSpan={5} className="pb-3"><Checks b={c.battery} /></td></tr>}
                      </>))}</tbody>
                  </table>
                  {r.rejected && r.rejected.length > 0 && <details className="mt-3"><summary className="note cursor-pointer">Other top rules that were not tested further ({r.rejected.length})</summary>
                    <ul className="note mt-1 list-disc pl-5">{r.rejected.map((x) => <li key={x.rule}>{x.rule}: discovery {pct(x.discovery.win, 1)} of {x.discovery.n.toLocaleString()} → confirmation {x.confirm.win == null ? '–' : pct(x.confirm.win, 1)}</li>)}</ul></details>}
                </>}
              </Card>

              <Card title="Stage 3 · Redesigning strategies with the edge">
                {r.outcome.status === 'no_edge' || r.outcome.status === 'no_data' ? <div className="note">Not run: there is no edge to redesign with. When a rule passes, every live / paper strategy is tested as a copy that must also satisfy it, and only copies that improve on the newest data become paper challengers.</div>
                  : r.redesign.length === 0 ? <div className="note">No strategy could take the rule (indicator clashes or non-standard specs).</div> : (
                    <table className="w-full text-sm"><thead><tr className="text-left text-mut"><th className="py-1">Strategy</th><th>Before (newest 30%)</th><th>With the edge rule</th><th>Verdict</th></tr></thead>
                      <tbody>{r.redesign.map((x) => (
                        <tr key={x.strategy_id} className="border-t border-line"><td className="py-1.5">{x.name}</td>
                          <td className="tabular-nums">{x.base.newest ? `${pct(x.base.newest.win, 1)} of ${x.base.newest.n.toLocaleString()}` : '–'}</td>
                          <td className="tabular-nums">{x.redesigned.newest ? `${pct(x.redesigned.newest.win, 1)} of ${x.redesigned.newest.n.toLocaleString()}` : '–'}</td>
                          <td><Badge tone={x.improved ? 'up' : 'mut'}>{x.improved ? 'IMPROVED' : x.redesigned.verdict}</Badge></td></tr>))}</tbody></table>)}
              </Card>
            </>
          )}

          {s.runs.length > 1 && <Card title="Earlier searches"><table className="w-full text-sm"><tbody>{s.runs.slice(1).map((x) => (
            <tr key={x.id} className="border-t border-line"><td className="py-1">{ago(x.started)}</td><td><Badge tone={x.outcome === 'edge_found' ? 'up' : x.outcome === 'possible_edge' ? 'warn' : 'mut'}>{(x.outcome ?? '').replace('_', ' ')}</Badge></td>
              <td className="note">{x.why}</td></tr>))}</tbody></table></Card>}
        </div>
      )}
    </>
  );
}
