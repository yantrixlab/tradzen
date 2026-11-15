import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, pct } from '../format';
import { Badge, Card, Empty, ErrorBox, PageTitle, Progress, Spinner, Stat } from '../components/ui';

interface Fold { fold: number; train: number; test: number; auc: number; auc_control: number }
interface Cell { dim: string; cell: string; n: number; win: number; z: number; p: number; win_first: number | null; win_second: number | null; flag: 'favour' | 'avoid' | null }
interface AiReport {
  trained_at: number; rows: number; pairs: number; features: number; verdict: 'proven' | 'weak' | 'no_skill' | 'need_data' | 'control_failed'; why: string;
  auc: number; auc_lo: number; auc_hi: number; auc_control: number; n_test: number; folds: Fold[];
  top: Record<string, { n: number; win: number; z_vs_breakeven: number }>;
  map: { confident_n: number; confident_win: number | null; breakeven: number; tested: number; cells: Cell[]; favour: Cell[]; avoid: Cell[] };
}
interface AiStatus {
  trained: boolean; usable: boolean; training: { running: boolean; progress: number; error: string | null; started: number | null }; report: AiReport | null;
  live: { n: number; acc: number | null; ci_lo: number | null; ci_hi: number | null; confident_n: number; confident_acc: number | null }; schedule_hour: number | null;
}

const VERDICT: Record<string, { tone: string; label: string }> = {
  proven: { tone: 'up', label: 'PROVEN SKILL' }, weak: { tone: 'warn', label: 'WEAK' }, no_skill: { tone: 'mut', label: 'NO PROVEN SKILL' },
  need_data: { tone: 'warn', label: 'NEED DATA' }, control_failed: { tone: 'down', label: 'CONTROL FAILED' },
};

function CellRows({ cells }: { cells: Cell[] }) {
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-mut"><th className="py-1">Situation</th><th>Confident calls</th><th>Win</th><th>1st half</th><th>2nd half</th><th /></tr></thead>
      <tbody>
        {cells.map((c) => (
          <tr key={c.dim + c.cell} className="border-t border-line">
            <td className="py-1">{c.dim}: <b>{c.cell}</b></td><td>{c.n.toLocaleString()}</td><td>{pct(c.win)}</td>
            <td>{c.win_first == null ? '–' : pct(c.win_first)}</td><td>{c.win_second == null ? '–' : pct(c.win_second)}</td>
            <td>{c.flag && <Badge tone={c.flag === 'favour' ? 'up' : 'down'}>{c.flag === 'favour' ? 'FAVOUR' : 'AVOID'}</Badge>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface RiskRow { id: number; name: string; status: string; analyzed: number | null; n: number | null; base_win: number | null; model: string | null; risk_model_used: boolean; rules: number; optimized: string | null }
interface RiskStatus { job: { running: boolean; strategy_id: number | null; progress: number; error: string | null }; strategies: RiskRow[] }
interface RiskRule { dimension: string; label: string; bucket: string; n: number; acc: number; baseline: number; q: number; validation: { n: number; acc: number | null; baseline: number | null }; text: string }
interface Seg { trades: number; settled: number; accuracy: number | null; breakeven: number | null; expectancy: number | null }
interface RiskReport {
  strategy: string; n_decided: number; base_win: number | null; breakeven: number; trained_at: number;
  model: { verdict: string; why: string; auc?: number; auc_lo?: number; auc_hi?: number };
  risky: { frac: number; useful: boolean; why: string; all: { risky_n: number; risky_win: number | null; rest_n: number; rest_win: number | null } } | null;
  rules: RiskRule[];
  optimized: { verdict: string; has_spec: boolean; filters: string[]; tried: { text: string; kept: boolean; why: string }[]; comparison: { valid: { base: Seg; candidate: Seg } | null; reason: string } | null } | null;
}

const OPT: Record<string, { tone: string; text: string }> = {
  holds: { tone: 'up', text: 'Improved on the newest trades' }, weak: { tone: 'warn', text: 'Slight improvement - within chance' }, noise: { tone: 'mut', text: 'No improvement - pattern did not hold' },
  too_few: { tone: 'warn', text: 'Too few trades left to judge' }, inconclusive: { tone: 'mut', text: 'Inconclusive' }, nothing_held: { tone: 'mut', text: 'No filter held up on newer trades' }, none: { tone: 'mut', text: 'No loss pattern confirmed' },
};

function RiskDetail({ id }: { id: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['risk', id], queryFn: () => api<RiskReport>(`/api/risk/${id}`), retry: false });
  const apply = useMutation({ mutationFn: () => api<{ strategy_id: number }>(`/api/risk/${id}/apply`, { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }) });
  if (q.isLoading) return <Spinner />;
  if (q.isError || !q.data) return <div className="note">Not analysed yet - press Analyse.</div>;
  const r = q.data, o = r.optimized, v = o?.comparison?.valid;
  return (
    <div className="space-y-3 text-sm">
      <div>Studied <b>{r.n_decided.toLocaleString()}</b> backtest trades: {r.base_win == null ? '–' : pct(r.base_win)} won (break-even {pct(r.breakeven)}).</div>
      <div><b>Risk model:</b> <Badge tone={r.risky?.useful ? 'up' : 'mut'}>{r.risky?.useful ? 'USED' : 'NOT USED'}</Badge> <span className="note">{r.risky ? r.risky.why : r.model.why}</span></div>
      {!r.risky?.useful && r.risky && (
        <div className="note">"Not used" does not mean there were no losing trades - {r.base_win == null ? 'about half' : pct(1 - r.base_win)} of this strategy's trades lost. It means the losers looked the same as the winners at entry: the trades the model rated riskiest won {r.risky.all.risky_win == null ? '–' : pct(r.risky.all.risky_win)} against {r.risky.all.rest_win == null ? '–' : pct(r.risky.all.rest_win)} for the rest, so there is nothing reliable to avoid. A model that is not better than chance on unseen trades must not tag or block signals.</div>
      )}
      {!r.risky?.useful && r.risky && (
        <div className="note">"Not used" does not mean there were no losing trades - {r.base_win == null ? 'about half' : pct(1 - r.base_win)} of this strategy's trades lost. It means the losers looked the same as the winners at entry: the trades the model rated riskiest won {r.risky.all.risky_win == null ? '–' : pct(r.risky.all.risky_win)} against {r.risky.all.rest_win == null ? '–' : pct(r.risky.all.rest_win)} for the rest, so there is nothing reliable to avoid. A model that is not better than chance on unseen trades must not tag or block signals.</div>
      )}
      <div>
        <b>Confirmed loss situations ({r.rules.length}):</b>
        {r.rules.length === 0 ? <span className="note"> none - no situation lost clearly more than the rest, confirmed on newer trades.</span> : (
          <ul className="mt-1 list-disc pl-5">{r.rules.map((x) => <li key={x.dimension + x.bucket}>{x.label}: <b>{x.bucket}</b> - won {pct(x.acc)} of {x.n} (average {pct(x.baseline)}); newest trades: {x.validation.acc == null ? '–' : pct(x.validation.acc)} of {x.validation.n}. Live signals matching this get the ⚠ RISKY tag.</li>)}</ul>
        )}
      </div>
      {o && (
        <div className="rounded-lg border border-line bg-panel2 p-3">
          <div><b>Improved version:</b> <Badge tone={OPT[o.verdict]?.tone ?? 'mut'}>{OPT[o.verdict]?.text ?? o.verdict}</Badge></div>
          {o.filters.length > 0 && <div className="mt-1">Adds: {o.filters.join('; ')}.</div>}
          {v && <div className="mt-1">On the newest trades (never used to build the rules): {v.base.accuracy == null ? '–' : pct(v.base.accuracy)} → <b>{v.candidate.accuracy == null ? '–' : pct(v.candidate.accuracy)}</b>, trades {v.base.settled} → {v.candidate.settled}, break-even {v.candidate.breakeven == null ? '–' : pct(v.candidate.breakeven)}.</div>}
          <ul className="note mt-1 list-disc pl-5">{o.tried.map((t) => <li key={t.text}>{t.kept ? 'kept' : 'dropped'}: {t.text} - {t.why}</li>)}</ul>
          {o.has_spec && <div className="mt-2"><button className="btn btn-primary" disabled={apply.isPending || apply.isSuccess} onClick={() => apply.mutate()}>{apply.isSuccess ? 'Created as a paper strategy ✓' : 'Create as new paper strategy'}</button>
            <span className="note ml-2">The original is not changed. Watch the new one live before trusting it.</span></div>}
          {apply.isError && <ErrorBox error={apply.error} />}
        </div>
      )}
    </div>
  );
}

function RiskAdvisor() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['risk-status'], queryFn: () => api<RiskStatus>('/api/risk'), refetchInterval: (s) => (s.state.data?.job.running ? 2000 : 15000) });
  const go = useMutation({ mutationFn: (id: number) => api(`/api/risk/${id}/analyze`, { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['risk-status'] }) });
  const [open, setOpen] = useState<number | null>(null);
  const s = q.data;
  const rows = (s?.strategies ?? []).filter((x) => x.status !== 'archived').sort((a, b) => Number(b.status === 'live') - Number(a.status === 'live') || (b.analyzed ?? 0) - (a.analyzed ?? 0));
  return (
    <Card title="Risk advisor - learn from backtest losses">
      <div className="note mb-3">For each strategy it replays every stored candle, studies where its backtest trades lost, and keeps only what also holds on its newest trades. Live signals in such a situation get a ⚠ RISKY tag, and a filtered copy of the strategy is proposed as a new paper strategy. Most strategies show nothing here: that means their losses look like plain chance.</div>
      {!s ? <Spinner /> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-mut"><th className="py-1">Strategy</th><th>Backtest trades</th><th>Won</th><th>Loss rules</th><th>Risk model</th><th>Improved version</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <>
                <tr key={r.id} className="cursor-pointer border-t border-line hover:bg-panel2" onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td className="py-1.5">{r.name} {r.status === 'live' && <Badge tone="up">live</Badge>}</td>
                  <td>{r.n == null ? '–' : r.n.toLocaleString()}</td><td>{r.base_win == null ? '–' : pct(r.base_win)}</td><td>{r.analyzed ? r.rules : '–'}</td>
                  <td>{!r.analyzed ? '–' : r.risk_model_used ? <Badge tone="up">used</Badge> : <span className="note">not used</span>}</td>
                  <td>{r.optimized ? <Badge tone={OPT[r.optimized]?.tone ?? 'mut'}>{OPT[r.optimized]?.text ?? r.optimized}</Badge> : '–'}</td>
                  <td className="text-right"><button className="btn" disabled={s.job.running || go.isPending} onClick={(e) => { e.stopPropagation(); go.mutate(r.id); }}>
                    {s.job.running && s.job.strategy_id === r.id ? `${Math.round(s.job.progress * 100)}%` : r.analyzed ? 'Re-analyse' : 'Analyse'}</button></td>
                </tr>
                {open === r.id && <tr key={`${r.id}d`}><td colSpan={7} className="pb-3"><RiskDetail id={r.id} /></td></tr>}
              </>
            ))}
          </tbody>
        </table>
      )}
      {s?.job.error && <div className="note mt-2 text-down">{s.job.error}</div>}
    </Card>
  );
}

interface Health {
  id: number; name: string; status: string; tag: string; why: string;
  live: { n: number; wins: number; acc: number | null; lo: number | null; hi: number | null; breakeven: number; recent_acc: number | null; recent_n: number };
  backtest: { n: number; win: number; breakeven: number } | null; rules: number | null;
}
const TAG: Record<string, { tone: string; label: string }> = {
  upgrade: { tone: 'up', label: 'UPGRADE AVAILABLE' }, pause: { tone: 'down', label: 'NO EDGE · PAUSE' }, improve: { tone: 'warn', label: 'NEEDS IMPROVEMENT' }, analyse: { tone: 'warn', label: 'NOT STUDIED' },
  testing: { tone: 'acc', label: 'UPGRADE TESTING' }, watch: { tone: 'mut', label: 'WATCH' }, collecting: { tone: 'mut', label: 'COLLECTING' }, keep: { tone: 'up', label: 'KEEP' }, paused: { tone: 'mut', label: 'PAUSED' },
};

function StrategyHealth() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['strategy-health'], queryFn: () => api<{ strategies: Health[]; counts: Record<string, number> }>('/api/strategy-health'), refetchInterval: 20000 });
  const refresh = () => { qc.invalidateQueries({ queryKey: ['strategy-health'] }); qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }); };
  const pause = useMutation({ mutationFn: (ids: number[]) => api('/api/strategies/bulk', { body: { ids, status: 'paused' } }), onSuccess: refresh });
  const analyse = useMutation({ mutationFn: (id: number) => api(`/api/risk/${id}/analyze`, { body: {} }), onSuccess: refresh });
  const apply = useMutation({ mutationFn: (id: number) => api(`/api/risk/${id}/apply`, { body: {} }), onSuccess: refresh });
  const [all, setAll] = useState(false);
  const list = q.data?.strategies ?? [];
  const liveLosers = list.filter((s) => s.status === 'live' && s.tag === 'pause');
  const shown = all ? list : list.filter((s) => s.status !== 'paused' && !['watch', 'collecting'].includes(s.tag) || s.status === 'live');
  return (
    <Card title="Strategy health - what to improve, upgrade or pause"
      right={liveLosers.length > 0 ? <button className="btn border-down/50 text-down hover:bg-down/10" disabled={pause.isPending}
        onClick={() => window.confirm(`Pause ${liveLosers.length} LIVE strateg${liveLosers.length === 1 ? 'y' : 'ies'} that are proven below break-even?\n\n${liveLosers.map((s) => '• ' + s.name).join('\n')}\n\nThey stop sending alerts. Nothing is deleted and you can resume them any time.`) && pause.mutate(liveLosers.map((s) => s.id))}>
        Pause {liveLosers.length} losing live strateg{liveLosers.length === 1 ? 'y' : 'ies'}</button> : undefined}>
      <div className="note mb-3">
        Each strategy is judged on its real settled signals <i>and</i> its backtest. "No edge" is said only when even the best case of its win rate (95% range) is below break-even - luck cannot condemn a strategy. The AI can only upgrade a strategy that has a weak spot to cut away (a loss pattern that holds on newer trades); a strategy that simply wins about 50% has no edge to improve, and the honest move is to pause it.
      </div>
      {!q.data ? <Spinner /> : (
        <>
          <div className="mb-2 flex flex-wrap gap-1.5">{Object.entries(q.data.counts).filter(([, n]) => n > 0).map(([t, n]) => <Badge key={t} tone={TAG[t]?.tone ?? 'mut'}>{TAG[t]?.label ?? t} · {n}</Badge>)}</div>
          <table className="w-full text-sm">
            <thead><tr className="text-left text-mut"><th className="py-1">Strategy</th><th>Real signals</th><th>Backtest</th><th>Verdict</th><th /></tr></thead>
            <tbody>{shown.map((s) => (
              <tr key={s.id} className="border-t border-line align-top">
                <td className="py-1.5">{s.name} {s.status === 'live' && <Badge tone="up">live</Badge>}</td>
                <td className="tabular-nums">{s.live.n ? <>{s.live.n} · <b>{pct(s.live.acc ?? 0)}</b> <span className="note">({s.live.lo == null || s.live.hi == null ? '–' : `${pct(s.live.lo)}–${pct(s.live.hi)}`}, BE {pct(s.live.breakeven)})</span>{s.live.recent_n >= 30 && s.live.recent_acc != null ? <div className="note">last {s.live.recent_n}: {pct(s.live.recent_acc)}</div> : null}</> : '–'}</td>
                <td className="tabular-nums">{s.backtest ? <>{pct(s.backtest.win)} <span className="note">of {s.backtest.n.toLocaleString()} (BE {pct(s.backtest.breakeven)})</span></> : '–'}</td>
                <td><Badge tone={TAG[s.tag]?.tone ?? 'mut'}>{TAG[s.tag]?.label ?? s.tag}</Badge><div className="note">{s.why}</div></td>
                <td className="whitespace-nowrap text-right">
                  {s.tag === 'upgrade' && <button className="btn btn-primary" disabled={apply.isPending} onClick={() => apply.mutate(s.id)}>Create improved copy</button>}
                  {(s.tag === 'analyse' || s.tag === 'improve') && <button className="btn" disabled={analyse.isPending} onClick={() => analyse.mutate(s.id)}>Analyse</button>}
                  {s.tag === 'pause' && s.status !== 'paused' && <button className="btn" disabled={pause.isPending} onClick={() => pause.mutate([s.id])} title="Stops its alerts / signals; resume any time">Pause</button>}
                </td>
              </tr>))}</tbody>
          </table>
          <button className="note mt-2 underline" onClick={() => setAll(!all)}>{all ? 'Show only strategies needing attention' : `Show all ${list.length} strategies`}</button>
          {(pause.isError || apply.isError || analyse.isError) && <ErrorBox error={pause.error ?? apply.error ?? analyse.error} />}
        </>
      )}
    </Card>
  );
}

interface Upgrade {
  id: number; parent: string | null; child: string | null; created: number; status: 'testing' | 'promoted' | 'retired'; filters: string; decision: string | null;
  live: { n: number; wins: number; breakeven: number; need: number }; parent_live: { n: number; wins: number };
}

function UpgradeLog() {
  const q = useQuery({ queryKey: ['upgrades'], queryFn: () => api<{ upgrades: Upgrade[]; auto_upgrade: boolean; nightly: boolean; queue: number; min_live: number; max_live: number }>('/api/upgrades'), refetchInterval: 15000 });
  const d = q.data;
  const tone = { testing: 'warn', promoted: 'up', retired: 'mut' } as const;
  return (
    <Card title="Automatic upgrades - challengers">
      <div className="note mb-3">
        {d && !d.nightly ? 'Nightly re-checks are OFF (QXS_RISK_NIGHTLY). ' : 'Every night every live and paper strategy is re-studied. '}
        {d && !d.auto_upgrade ? 'Automatic challengers are OFF (QXS_AUTO_UPGRADE). ' : 'When a filter holds up on the newest backtest trades, a filtered copy starts as a PAPER challenger - never live. '}
        It is promoted to live only after {d?.min_live ?? 300}+ real signals prove its win rate is above break-even (95% lower bound); it is retired if it is proven worse, no better than its parent, or still unproven after {d?.max_live ?? 1000} signals. The original is never edited{d?.queue ? ` · ${d.queue} strategies queued for tonight's re-check` : ''}.
      </div>
      {!d ? <Spinner /> : d.upgrades.length === 0 ? <Empty>No challengers yet. They appear only when a filter really improves a strategy's newest backtest trades - none has so far.</Empty> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-mut"><th className="py-1">Challenger</th><th>Filters</th><th>Live signals</th><th>Parent (same period)</th><th>Status</th></tr></thead>
          <tbody>{d.upgrades.map((u) => (
            <tr key={u.id} className="border-t border-line align-top">
              <td className="py-1.5">{u.child}<div className="note">of {u.parent}</div></td><td>{u.filters}</td>
              <td>{u.live.n}/{u.live.need} · {u.live.n ? pct(u.live.wins / u.live.n) : '–'} <span className="note">(BE {pct(u.live.breakeven)})</span></td>
              <td>{u.parent_live.n ? `${u.parent_live.n} · ${pct(u.parent_live.wins / u.parent_live.n)}` : '–'}</td>
              <td><Badge tone={tone[u.status]}>{u.status.toUpperCase()}</Badge>{u.decision && <div className="note">{u.decision}</div>}</td>
            </tr>))}</tbody>
        </table>
      )}
    </Card>
  );
}

export default function AI() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['ai-status'], queryFn: () => api<AiStatus>('/api/ai/status'), refetchInterval: (s) => (s.state.data?.training.running ? 2000 : 10000) });
  const train = useMutation({ mutationFn: () => api<{ started: boolean }>('/api/ai/train', { body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-status'] }) });
  const s = q.data;
  const r = s?.report ?? null;
  const v = r ? VERDICT[r.verdict] ?? VERDICT.no_skill : null;

  return (
    <>
      <PageTitle title="AI advisor" sub="An open, dependency-free gradient-boosting model reads every closed 1-minute candle (56 price-action features: wicks, close position, engulfing, structure, zones…) and estimates the chance the next candle closes up. It is trained and tested on data it has never seen, and it is only allowed to confirm or veto a signal after it has proven skill there - and keeps proving it live." />
      <div className="mb-4"><StrategyHealth /></div>
      {!s ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <Card title="Model">
            <div className="space-y-3 text-sm">
              <div>{v ? <Badge tone={v.tone}>{v.label}</Badge> : <Badge tone="mut">NOT TRAINED</Badge>}</div>
              {r && <div className="note">{r.why}</div>}
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Trained" value={r ? ago(r.trained_at) : 'never'} />
                <Stat label="Candles" value={r ? r.rows.toLocaleString() : '–'} sub={r ? `${r.pairs} pairs · ${r.features} features` : undefined} />
                <Stat label="Unseen AUC" value={r ? r.auc.toFixed(3) : '–'} sub={r ? `95%: ${r.auc_lo.toFixed(3)}–${r.auc_hi.toFixed(3)}` : undefined} />
                <Stat label="Shuffled control" value={r ? r.auc_control.toFixed(3) : '–'} sub="should sit at 0.500" />
              </div>
              {s.training.running && <div><Badge tone="warn">TRAINING</Badge><div className="mt-1"><Progress value={s.training.progress} /></div></div>}
              {s.training.error && <div className="note text-down">{s.training.error}</div>}
              <button className="btn btn-primary w-full" disabled={s.training.running || train.isPending} onClick={() => train.mutate()}>{s.training.running ? 'Training…' : 'Train now'}</button>
              {train.isError && <ErrorBox error={train.error} />}
              <div className="note">Trains in a separate low-priority process, so live signals are not slowed. Retrains every night{s.schedule_hour != null ? ` at ${String(s.schedule_hour).padStart(2, '0')}:00 UTC` : ''} on everything collected so far.</div>
              <div className="note">AUC 0.500 = a coin flip; anything inside about ±0.005 is luck. A binary option needs about 54% right on its confident calls to break even.</div>
            </div>
          </Card>

          <div className="space-y-4">
            <Card title="Is it allowed to confirm signals?">
              <div className="text-sm">
                {s.usable ? <><Badge tone="up">YES</Badge> <span className="ml-2">It has proven skill on unseen data and its live record does not contradict it. Signals show AI agrees / disagrees.</span></>
                  : <><Badge tone="mut">NO</Badge> <span className="ml-2">It has not proven skill yet, so it confirms nothing and vetoes nothing - signals are shown as "AI: no proven skill". This is deliberate: a model that cannot beat chance on unseen data must not decide your trades.</span></>}
              </div>
            </Card>

            <Card title="Live record (candles it only saw live)">
              {s.live.n === 0 ? <Empty>No scored live calls yet. It logs one call per closed candle of each streaming pair, and scores it when the next candle closes.</Empty> : s.live.n < 100 ? (
                <div className="text-sm">{s.live.n} call{s.live.n === 1 ? '' : 's'} scored so far ({s.live.acc == null ? '–' : pct(s.live.acc)} right). <span className="note">Too few to mean anything - with fewer than 100 calls luck dominates (one call is always 0% or 100%). The record shows once there are 100+.</span></div>
              ) : (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Stat label="Calls scored" value={s.live.n.toLocaleString()} />
                  <Stat label="Direction right" value={s.live.acc == null ? '–' : pct(s.live.acc)} sub={s.live.ci_lo == null || s.live.ci_hi == null ? undefined : `95%: ${pct(s.live.ci_lo)}–${pct(s.live.ci_hi)}`} />
                  <Stat label="Confident calls" value={s.live.confident_n.toLocaleString()} />
                  <Stat label="Confident right" value={s.live.confident_acc == null ? '–' : pct(s.live.confident_acc)} />
                </div>
              )}
            </Card>

            {r && <Card title="Walk-forward test (always on later data than it trained on)">
              <table className="w-full text-sm">
                <thead><tr className="text-left text-mut"><th className="py-1">Fold</th><th>Trained on</th><th>Tested on</th><th>AUC</th><th>Shuffled control</th></tr></thead>
                <tbody>{r.folds.map((f) => <tr key={f.fold} className="border-t border-line"><td className="py-1">{f.fold}</td><td>{f.train.toLocaleString()}</td><td>{f.test.toLocaleString()}</td><td>{f.auc.toFixed(4)}</td><td>{f.auc_control.toFixed(4)}</td></tr>)}</tbody>
              </table>
              <div className="note mt-2">Most confident calls on unseen data: {Object.entries(r.top).map(([k, t]) => `${k.replace('top', 'top ')}% → ${pct(t.win)} right (${t.n.toLocaleString()})`).join(' · ')}. Break-even is about 54%.</div>
            </Card>}

            {r && <Card title={`Where to trade and where to avoid (${r.map.tested} situations tested)`}>
              {r.map.favour.length === 0 && r.map.avoid.length === 0
                ? <div className="note">No situation stands out once the many comparisons are corrected for - there is no hour, pair, volatility, structure or zone where the AI is reliably better or worse. That is the honest answer for now; this list fills in only when a pattern holds in both halves of the unseen period.</div>
                : <CellRows cells={[...r.map.favour, ...r.map.avoid]} />}
              <details className="mt-3"><summary className="cursor-pointer text-sm text-mut">Show the most extreme situations anyway (not significant unless flagged)</summary><div className="mt-2"><CellRows cells={r.map.cells.slice(0, 15)} /></div></details>
            </Card>}
          </div>
        </div>
      )}
      <div className="mt-4"><RiskAdvisor /></div>
      <div className="mt-4"><UpgradeLog /></div>
    </>
  );
}
