import { ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, pct, prettyPair } from '../format';
import { Badge, Card, DirBadge, Empty, ErrorBox, PageTitle, Spinner, Stat } from '../components/ui';

interface Trait { key: string; label: string; group: string; n: number; wins: number; rate: number | null; lo: number | null; hi: number | null; share: number; verdict: string; p: number; p_corrected: number }
interface PairRow { symbol: string; n: number; wins: number; rate: number; lo: number; hi: number; verdict: string }
interface Rule { key: string; name: string; thr_atr: number; from_s: number; to_s: number; skip_pairs: string[]; text: string; evidence: { n: number; wins: number; rate: number; verdict: string } }
interface Profile {
  n: number; wins: number; rate: number | null; lo: number | null; hi: number | null; be: number; p: number; positions: number; orders: number; steps: { n: number; wins: number }; need: number;
  timing: { median: number | null; at_open: number; late: number }; traits: Trait[]; pairs: PairRow[]; bullets: string[]; rule: Rule | null; compared: number; z: number;
}
interface SigRecord { n: number; wins: number; losses: number; acc: number | null; lo: number | null; hi: number | null; be: number | null; pending: number | null }
interface Sig { id: number; created: number; symbol: string; direction: string; entry: number; exit: number | null; expire_t: number; inc_atr: number; off_s: number; status: string; payout: number; step: string | null; step_entry: number | null; step_exit: number | null }
const stakes = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}`;
interface Mtg { mult: number; lost: number; n: number; wins: number; losses: number; pending: number; acc: number | null; cycles: number; net_flat: number; net_mtg: number }
interface Status { settings: { enabled: boolean; alert: 'earned' | 'all' | 'off'; min_payout: number }; profile: Profile; record: SigRecord; mtg: Mtg; recent: Sig[]; alerting: boolean; alert_note: string; now: number; streaming: string[] }

const UP = '#22c55e', DOWN = '#ef4444', WARN = '#f59e0b', ACC = '#8b5cf6', MUT = '#8a93b2';
const VERDICT: Record<string, [string, string, string]> = {
  proven: ['up', 'PROVEN', UP], promising: ['acc', 'PROMISING', ACC], weak: ['mut', 'WEAK', MUT], against: ['down', 'AGAINST', DOWN], few: ['warn', 'TOO FEW', WARN],
};
const ALERT_OPTIONS: [Status['settings']['alert'], string, string][] = [
  ['earned', 'Earned (recommended)', 'Paper first. Alerts only after 30+ settled signals at or above break-even.'],
  ['all', 'Every signal (experimental)', 'Alerts from the first signal, even though nothing is proven yet.'],
  ['off', 'No alerts', 'Signals are still recorded and settled on this page.'],
];

/** A win rate with its 95% range and the break-even mark, on one 0-100% line. */
function RateBar({ rate, lo, hi, be, color }: { rate: number | null; lo: number | null; hi: number | null; be: number; color: string }) {
  if (rate === null || lo === null || hi === null) return <div className="h-2.5 w-full rounded-full bg-panel2" />;
  return (
    <div className="relative h-2.5 w-full min-w-[150px] rounded-full bg-panel2" role="img" aria-label={`win rate ${pct(rate, 0)}, range ${pct(lo, 0)} to ${pct(hi, 0)}, break-even ${pct(be, 0)}`}>
      <div className="absolute top-0 h-full rounded-full opacity-35" style={{ left: `${lo * 100}%`, width: `${Math.max(1, (hi - lo) * 100)}%`, background: color }} />
      <div className="absolute -top-1 h-[18px] w-px bg-white/70" style={{ left: `${be * 100}%` }} title={`break-even ${pct(be, 0)}`} />
      <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg" style={{ left: `${rate * 100}%`, background: color }} />
    </div>
  );
}

const Section = ({ title, hint, children, right, className = '' }: { title: string; hint?: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) => (
  <Card title={title} right={right} className={className}>{hint && <p className="note mb-3">{hint}</p>}{children}</Card>
);

export default function MyStyle() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['style'], queryFn: () => api<Status>('/api/style'), refetchInterval: 5000, placeholderData: (p) => p });
  const save = useMutation({ mutationFn: (b: Partial<Status['settings']>) => api<Status>('/api/style/settings', { method: 'PUT', body: b }), onSuccess: (r) => qc.setQueryData(['style'], r) });
  if (q.isError && !q.data) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const s = q.data, p = s.profile, r = s.record, m = s.mtg;
  const groups = Array.from(new Set(p.traits.map((t) => t.group)));
  const lowSample = p.n < p.need;
  const be = p.be;

  return (
    <>
      <PageTitle title="My style" sub="What your own trades have in common, how well each habit really works, and a live rule that signals the same situation - measured honestly, so luck is not mistaken for skill." />

      {/* ---- the evidence ---- */}
      <section className="grid gap-4 lg:grid-cols-5">
        <div className="rounded-2xl border border-line p-5 lg:col-span-2" style={{ background: 'radial-gradient(600px 220px at 10% -20%, rgba(139,92,246,.16), transparent 60%), #111830' }}>
          <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">Your first trades</div>
          {p.n === 0 ? <Empty>No finished first trades with stored candles yet.</Empty> : (
            <>
              <div className="mt-1 flex items-baseline gap-2"><span className="text-5xl font-bold tabular-nums">{pct(p.rate, 0)}</span><span className="text-sm text-mut">won · {p.wins} of {p.n}</span></div>
              <div className="mt-3"><RateBar rate={p.rate} lo={p.lo} hi={p.hi} be={be} color={p.lo !== null && p.lo > be ? UP : p.rate !== null && p.rate > be ? ACC : DOWN} /></div>
              <div className="mt-1 flex justify-between text-[11px] text-mut"><span>95% range {pct(p.lo, 0)}–{pct(p.hi, 0)}</span><span>break-even {pct(be, 0)} (white mark)</span></div>
              <div className="mt-4">
                <div className="mb-1 flex justify-between text-xs text-mut"><span>Evidence collected</span><span className="tabular-nums">{p.n} / {p.need} trades</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-panel2"><div className="h-full rounded-full bg-acc transition-all" style={{ width: `${Math.min(100, (p.n / p.need) * 100)}%` }} /></div>
              </div>
              <p className="mt-3 rounded-lg border border-line bg-panel2 px-3 py-2 text-xs text-mut">
                {p.lo !== null && p.lo > be ? 'Your win rate is clearly above break-even.' : lowSample
                  ? <>Not yet distinguishable from luck (chance of doing this well by luck alone: <b className="text-fg">{pct(p.p, 0)}</b>). Keep trading: every trade sharpens the picture.</>
                  : <>Chance of doing this well by luck alone: <b className="text-fg">{pct(p.p, 0)}</b>.</>}
              </p>
            </>
          )}
        </div>
        <Card title="What the AI sees in your trades" className="lg:col-span-3">
          <ul className="space-y-2.5 text-sm leading-relaxed text-mut">{p.bullets.map((b) => <li key={b} className="flex gap-2"><span className="text-acc" aria-hidden="true">›</span><span>{b}</span></li>)}</ul>
        </Card>
      </section>

      {/* ---- the live rule ---- */}
      <div className="mt-4 grid gap-4 lg:grid-cols-5">
        <Card title="The live rule learned from your trades" className="lg:col-span-3" right={<Badge tone={!s.settings.enabled ? 'mut' : s.alerting ? 'up' : 'warn'}>{!s.settings.enabled ? 'OFF' : s.alerting ? 'ALERTING' : 'PAPER'}</Badge>}>
          {!p.rule ? (
            <Empty>No rule yet: none of your habits beats break-even in your own trades with enough trades behind it. It appears here as soon as one does.</Empty>
          ) : (
            <>
              <div className="rounded-xl border border-acc/40 bg-acc/10 p-4">
                <div className="flex items-center justify-between gap-2"><div className="text-lg font-bold">{p.rule.name}</div>
                  <Badge tone={(VERDICT[p.rule.evidence.verdict] ?? VERDICT.weak)[0]}>{(VERDICT[p.rule.evidence.verdict] ?? VERDICT.weak)[1]}</Badge></div>
                <p className="mt-2 text-sm leading-relaxed">{p.rule.text}</p>
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[['Move needed', `${p.rule.thr_atr} ATR`], ['Acts', `${p.rule.from_s}–${p.rule.to_s} s in`], ['Evidence', `${p.rule.evidence.wins}/${p.rule.evidence.n} won`], ['Skips', p.rule.skip_pairs.length ? p.rule.skip_pairs.map((x) => x.replace('_otc', '')).join(', ') : 'none']].map(([l, v]) => (
                  <div key={l} className="rounded-lg border border-line bg-panel2 p-2.5"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">{l}</dt><dd className="mt-0.5 text-sm font-semibold tabular-nums">{v}</dd></div>))}
              </dl>
              <p className="note mt-3">The numbers come from your own trades: how far price had moved when you faded it, and when in the candle you acted. It settles like your trades: a win if the candle closes on your side of the entry price.</p>
            </>
          )}
        </Card>
        <Card title="Signals & alerts" className="lg:col-span-2">
          <label className="flex cursor-pointer items-center gap-3">
            <button type="button" role="switch" aria-checked={s.settings.enabled} onClick={() => save.mutate({ enabled: !s.settings.enabled })}
              className={`relative h-5 w-9 shrink-0 rounded-full border transition ${s.settings.enabled ? 'border-acc bg-acc' : 'border-line bg-panel2'}`}>
              <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-all ${s.settings.enabled ? 'left-[18px]' : 'left-0.5'}`} />
            </button>
            <span className="text-sm font-medium">Run the rule on live prices</span>
          </label>
          <div className="mt-4 space-y-2" role="radiogroup" aria-label="Alerts">
            {ALERT_OPTIONS.map(([k, l, d]) => (
              <button key={k} type="button" role="radio" aria-checked={s.settings.alert === k} onClick={() => save.mutate({ alert: k })}
                className={`w-full rounded-lg border p-3 text-left transition ${s.settings.alert === k ? 'border-acc bg-acc/10' : 'border-line bg-panel2 hover:border-acc/50'}`}>
                <div className="text-sm font-semibold">{l}</div><div className="text-xs text-mut">{d}</div>
              </button>))}
          </div>
          <p className="mt-3 text-xs text-mut"><b className="text-fg">Right now:</b> {s.alert_note}.</p>
        </Card>
      </div>

      {/* ---- its own record ---- */}
      <Card title="The rule's own record" className="mt-4" right={<span className="note">the real test: live signals, settled at the candle close</span>}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Settled signals" value={r.n} sub={`${r.pending ?? 0} pending · need 30 to earn alerts`} />
          <Stat label="Win rate" value={pct(r.acc, 0)} tone={r.acc !== null && r.be !== null ? (r.acc >= r.be ? 'up' : 'down') : undefined} sub={r.n ? `${r.wins} won · ${r.losses} lost${r.lo !== null ? ` · 95% ${pct(r.lo, 0)}–${pct(r.hi, 0)}` : ''}` : 'no settled signal yet'} />
          <Stat label="Break-even" value={pct(r.be ?? be, 0)} sub="at the pairs' payouts" />
          <Stat label="One-step MTG" value={m.n ? `${m.wins} / ${m.n}` : '–'} tone={m.n ? (m.acc !== null && m.acc >= (r.be ?? be) ? 'up' : 'down') : undefined}
            sub={m.n ? `${pct(m.acc, 0)} of steps won · after ${m.lost} lost signal${m.lost === 1 ? '' : 's'}${m.pending ? ` · ${m.pending} pending` : ''}` : m.pending ? `${m.pending} step pending` : 'no lost signal yet'} />
          <Stat label="Streaming now" value={s.streaming.length} sub={s.streaming.length ? `${s.streaming.length} pair${s.streaming.length === 1 ? '' : 's'} watched live` : 'open Quotex to stream prices'} />
        </div>
        {m.cycles > 0 && (
          <p className="note mt-3">
            <b className="text-fg">One-step martingale:</b> after a lost signal, the same side again on the next candle with {m.mult}× the stake. Over {m.cycles} finished signal{m.cycles === 1 ? '' : 's'}, in units of the first stake:
            {' '}flat <b className={m.net_flat >= 0 ? 'text-up' : 'text-down'}>{stakes(m.net_flat)}</b>, with the step <b className={m.net_mtg >= 0 ? 'text-up' : 'text-down'}>{stakes(m.net_mtg)}</b>.
            {' '}The step turns a loss into a win only while steps win more than about {pct(r.be ?? be, 0)} of the time; it cannot create an edge.
          </p>
        )}
        {s.recent.length === 0 ? <Empty>No signal yet. It fires when a streaming pair makes the move the rule fades, inside the rule's time window.</Empty> : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>{['When', 'Pair', 'Side', 'Move', 'Entered', 'Price in → out', 'Result', 'One step'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{s.recent.slice(0, 15).map((x) => (
                <tr key={x.id}>
                  <td className="td whitespace-nowrap text-mut">{ago(x.created, s.now)}</td><td className="td whitespace-nowrap font-medium">{prettyPair(x.symbol)}</td>
                  <td className="td whitespace-nowrap"><DirBadge dir={x.direction} /></td>
                  <td className="td tabular-nums">{x.inc_atr > 0 ? '+' : ''}{x.inc_atr.toFixed(2)} ATR</td><td className="td tabular-nums text-mut">{Math.round(x.off_s)} s in</td>
                  <td className="td whitespace-nowrap tabular-nums text-mut">{x.entry} → {x.exit ?? '…'}</td>
                  <td className="td"><Badge tone={x.status === 'win' ? 'up' : x.status === 'loss' ? 'down' : x.status === 'pending' ? 'warn' : 'mut'}>{x.status.toUpperCase()}</Badge></td>
                  <td className="td whitespace-nowrap">
                    {x.status !== 'loss' || !x.step ? <span className="text-mut">–</span> : (
                      <span className="inline-flex items-center gap-2">
                        <Badge tone={x.step === 'win' ? 'up' : x.step === 'loss' ? 'down' : x.step === 'pending' ? 'warn' : 'mut'}>{x.step === 'void' ? 'NOT PLAYED' : x.step.toUpperCase()}</Badge>
                        {x.step_exit !== null && x.step_entry !== null && <span className="text-xs tabular-nums text-mut">{x.step_entry} → {x.step_exit}</span>}
                      </span>
                    )}
                  </td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ---- habits ---- */}
      <Section title="Your habits, one by one" className="mt-4" hint={<>Each habit: how many of your first trades had it, how many of those won, and the win rate with its range - <b className="text-fg">widened for the {p.compared} habits compared</b>, so a lucky streak is not mistaken for a skill. The white mark is break-even.</>}>
        {p.n === 0 ? <Empty>Habits appear once you have finished first trades.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>{['Habit', 'Your trades', 'Won', 'Win rate and range', 'Verdict'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{groups.flatMap((g) => [
                <tr key={`g-${g}`}><td colSpan={5} className="bg-panel2/60 px-2 py-1 text-[10.5px] font-semibold uppercase tracking-wider text-mut">{g}</td></tr>,
                ...p.traits.filter((t) => t.group === g).sort((a, b) => b.n - a.n).map((t) => {
                  const v = VERDICT[t.verdict] ?? VERDICT.weak;
                  return (
                    <tr key={t.key} className={t.n === 0 ? 'opacity-45' : ''}>
                      <td className="td">{t.label}</td>
                      <td className="td tabular-nums">{t.n}<span className="ml-1 text-xs text-mut">({pct(t.share, 0)})</span></td>
                      <td className="td tabular-nums">{t.wins}<span className="text-mut"> / {t.n - t.wins}</span></td>
                      <td className="td w-[34%]"><div className="flex items-center gap-3"><div className="flex-1"><RateBar rate={t.rate} lo={t.lo} hi={t.hi} be={be} color={v[2]} /></div><span className="w-10 text-right text-xs tabular-nums text-mut">{t.rate === null ? '–' : pct(t.rate, 0)}</span></div></td>
                      <td className="td"><span title={t.n ? `chance of this by luck alone: ${pct(t.p, 0)} (${pct(t.p_corrected, 0)} after correcting for ${p.compared} habits)` : ''}><Badge tone={v[0]}>{v[1]}</Badge></span></td>
                    </tr>);
                }),
              ])}</tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="By pair" className="mt-4" hint="Pairs behave differently - a pair where you keep losing is skipped by the live rule once you have 5 trades on it.">
        {p.pairs.length === 0 ? <Empty>No pair data yet.</Empty> : (
          <table className="w-full text-sm">
            <thead><tr>{['Pair', 'Trades', 'Won', 'Win rate and range', 'Verdict'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{p.pairs.map((x) => {
              const v = VERDICT[x.verdict] ?? VERDICT.weak;
              return (
                <tr key={x.symbol}><td className="td font-medium">{prettyPair(x.symbol)}</td><td className="td tabular-nums">{x.n}</td><td className="td tabular-nums">{x.wins}<span className="text-mut"> / {x.n - x.wins}</span></td>
                  <td className="td w-[34%]"><div className="flex items-center gap-3"><div className="flex-1"><RateBar rate={x.rate} lo={x.lo} hi={x.hi} be={be} color={v[2]} /></div><span className="w-10 text-right text-xs tabular-nums text-mut">{pct(x.rate, 0)}</span></div></td>
                  <td className="td"><Badge tone={v[0]}>{v[1]}</Badge></td></tr>);
            })}</tbody>
          </table>
        )}
      </Section>

      <p className="note mt-4">Only first trades count as decisions: martingale steps ({p.steps.n} so far, {p.steps.wins} won) and repeated clicks on one candle are forced by an earlier result. Habits are measured against your own market context at the moment you entered, from the stored candles only (nothing from the future).</p>
    </>
  );
}
