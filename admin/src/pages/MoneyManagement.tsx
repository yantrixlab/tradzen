import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { ago, pct } from '../format';
import { currencySymbol, useAccount, useMoney } from '../hooks';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner } from '../components/ui';
import { Num, Row, Seg, Toggle } from '../components/settingsControls';
import {
  clampSettings, dots, LEVEL_COLOR, meterLevel, MmSettings, MmWhat, MoneyMgmt as Data, presetOf, PRESETS, share, stakePct, streakText, theme,
} from '../lib/moneyMgmt';

const UP = '#22c55e', DOWN = '#ef4444', WARN = '#f59e0b';
const noAccount = ({ account: _a, ...rest }: MmSettings) => rest;                      // eslint-disable-line @typescript-eslint/no-unused-vars

/** A thin bar with a label above it: how much of a limit is used (green, amber from half, red from 80%). */
function Meter({ label, value, note, p, color, ticks }: { label: string; value: ReactNode; note?: ReactNode; p: number | null; color?: string; ticks?: number }) {
  const c = color ?? LEVEL_COLOR[meterLevel(p)];
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2"><span className="text-[11px] font-semibold uppercase tracking-wider text-mut">{label}</span><span className="text-sm font-semibold tabular-nums">{value}</span></div>
      <div className="mt-1.5 flex h-2.5 gap-1" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((p ?? 0) * 100)}>
        {ticks ? Array.from({ length: ticks }, (_, i) => (
          <div key={i} className="h-full flex-1 overflow-hidden rounded-full bg-panel2"><div className="h-full rounded-full transition-all duration-500" style={{ width: (p ?? 0) * ticks > i ? '100%' : '0%', background: c }} /></div>
        )) : (
          <div className="h-full w-full overflow-hidden rounded-full bg-panel2"><div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(100, (p ?? 0) * 100)}%`, background: c }} /></div>
        )}
      </div>
      {note && <div className="mt-1 text-xs text-mut">{note}</div>}
    </div>
  );
}

const Tile = ({ label, children, sub, tone }: { label: string; children: ReactNode; sub?: ReactNode; tone?: 'up' | 'down' | 'warn' }) => (
  <div className="min-w-0 rounded-xl border border-line bg-panel2/70 p-3">
    <div className="text-[10.5px] font-semibold uppercase tracking-wider text-mut">{label}</div>
    <div className={`mt-0.5 break-words text-lg font-bold leading-tight tabular-nums sm:text-xl ${tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down' : tone === 'warn' ? 'text-warn' : ''}`}>{children}</div>
    {sub && <div className="mt-1 text-xs leading-snug text-mut">{sub}</div>}
  </div>
);

function WhatIf({ title, w, money, tone }: { title: string; w: MmWhat; money: (x: number | null | undefined, sign?: boolean) => string; tone: 'up' | 'down' }) {
  const th = theme(w.status, w.kind);
  return (
    <div className="min-w-0 rounded-xl border bg-panel2/60 p-3" style={{ borderColor: `${th.color}55` }}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2"><span className={`text-xs font-bold uppercase tracking-wider ${tone === 'up' ? 'text-up' : 'text-down'}`}>{title}</span><span className={`text-sm font-semibold tabular-nums ${tone === 'up' ? 'text-up' : 'text-down'}`}>{money(w.result, true)}</span></div>
      <div className="mt-2 text-[10.5px] font-semibold uppercase tracking-wider text-mut">Then stake</div>
      <div className="text-2xl font-bold tabular-nums" style={{ color: th.color }}>{w.status === 'stop' ? 'STOP' : money(w.stake)}</div>
      <div className="mt-1 text-xs leading-snug text-mut">{w.status === 'stop' ? w.reason : w.headline}</div>
    </div>
  );
}

export default function MoneyManagement() {
  const qc = useQueryClient();
  const money = useMoney();
  const acc = useAccount();
  const sym = currencySymbol(acc.data?.currency) || '¤';
  const tz = -new Date().getTimezoneOffset();
  const q = useQuery({ queryKey: ['moneymgmt'], queryFn: () => api<Data>(`/api/moneymgmt?tz=${tz}`), refetchInterval: 3000, placeholderData: (p) => p });
  const d = q.data;
  const saved = d?.settings ?? null;
  const [draft, setDraft] = useState<MmSettings | null>(null);
  const [copied, setCopied] = useState(false);
  const dirty = !!draft && !!saved && JSON.stringify(noAccount(draft)) !== JSON.stringify(noAccount(saved));
  useEffect(() => { if (saved && !draft) setDraft(saved); }, [saved, draft]);
  useEffect(() => { if (saved && draft && !dirty) setDraft(saved); }, [saved]);               // eslint-disable-line react-hooks/exhaustive-deps

  const put = useMutation({
    mutationFn: (s: Partial<MmSettings>) => api<Data>(`/api/moneymgmt/settings?tz=${tz}`, { method: 'PUT', body: s }),
    onSuccess: (r) => { qc.setQueryData(['moneymgmt'], r); setDraft((cur) => (cur ? { ...r.settings } : r.settings)); },
  });
  const patch = (p: Partial<MmSettings>) => setDraft((x) => (x ? clampLoose({ ...x, ...p }) : x));
  const nest = <K extends 'recovery' | 'loss' | 'target' | 'streak'>(k: K, p: Partial<MmSettings[K]>) => setDraft((x) => (x ? { ...x, [k]: { ...x[k], ...p } } : x));
  const copy = async () => {
    if (!d?.advice.stake) return;
    try { await navigator.clipboard.writeText(String(d.advice.stake)); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { /* clipboard not allowed here */ }
  };

  const bal = d?.balance.value ?? null;
  const hints = useMemo(() => ({ base: share(bal, draft?.base_pct ?? 0), max: share(bal, draft?.max_pct ?? 0), loss: share(d?.balance.start ?? bal, draft?.loss.pct ?? 0), tgt: share(d?.balance.start ?? bal, draft?.target.pct ?? 0) }), [bal, d?.balance.start, draft]);

  if (q.isError && !d) return <ErrorBox error={q.error} />;
  if (!d || !draft) return <Spinner />;
  const a = d.advice, th = theme(a.status, a.kind), stopped = a.status === 'stop';
  const net = d.today.net, edge = d.edge;
  const wr = edge.win_rate;
  const accLabel = d.account === 'demo' ? 'Demo' : 'Real';

  return (
    <>
      <PageTitle title="Money management" sub="How much to stake on your next trade, worked out from your balance and today's results. It only advises - it never places or changes a trade."
        right={<>
          <span className="chip border-line bg-panel2 text-mut" title="The page refreshes by itself every few seconds"><span className="live-dot mr-1.5 inline-block h-2 w-2 rounded-full bg-up" aria-hidden="true" />{d.balance.at ? `live · balance seen ${ago(d.balance.at, d.now)}` : 'live · waiting for your balance'}</span>
          <Seg label="Account" value={d.account} onChange={(v) => put.mutate({ account: v })} options={[['real', 'Real'], ['demo', 'Demo']]} />
        </>} />

      {/* ---- the answer ---- */}
      <section className="relative overflow-hidden rounded-2xl border p-4 sm:p-6" style={{ borderColor: `${th.color}55`, background: `radial-gradient(900px 300px at 10% -25%, ${th.glow}, transparent 62%), #111830` }} aria-live="polite">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,440px)] lg:gap-8">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={th.tone}>{th.icon} {th.label}</Badge>
              <span className="text-sm font-semibold text-fg">{a.headline}</span>
            </div>
            <div className="mt-4 text-[11px] font-semibold uppercase tracking-widest text-mut">Next trade · stake</div>
            <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3">
              <span className="text-5xl font-extrabold leading-none tracking-tight tabular-nums sm:text-7xl" style={{ color: th.color }}>
                {a.stake === null ? '–' : stopped ? 'STOP' : money(a.stake)}
              </span>
              {a.stake ? <span className="text-sm text-mut">{stakePct(a.stake, bal)} of your {accLabel.toLowerCase()} balance</span> : null}
            </div>
            <p className="mt-3 max-w-prose text-[15px] leading-snug text-fg/90">{a.reason}</p>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {a.stake ? <button type="button" className="btn" onClick={copy} aria-label="Copy the stake amount">{copied ? '✓ Copied' : '⧉ Copy amount'}</button> : null}
              {(a.kind === 'manual' || a.kind === 'cooldown') && <Link to="/guard" className="btn">Open the Trading guard</Link>}
              {!!a.stake && a.base !== null && a.stake !== a.base && !stopped && <span className="note">Base stake is {money(a.base)}</span>}
            </div>
          </div>
          <div className="grid min-w-0 grid-cols-2 gap-2.5 self-start">
            <Tile label={`${accLabel} balance`} sub={d.balance.start !== null ? `Day started at ${money(d.balance.start)}` : 'Waiting for the balance'}>{money(bal)}</Tile>
            <Tile label="Today's result" tone={net > 0 ? 'up' : net < 0 ? 'down' : undefined} sub={`${d.today.trades} trade${d.today.trades === 1 ? '' : 's'} · ${d.today.wins}W ${d.today.losses}L${d.today.running ? ` · ${d.today.running} running` : ''}`}>{money(net, true)}</Tile>
            <Tile label="Streak" tone={d.streak.kind === 'win' ? 'up' : d.streak.kind === 'loss' ? 'down' : undefined} sub={d.streak.kind ? (d.streak.kind === 'win' ? 'keep the stake steady' : a.debt > 0 ? `${money(a.debt)} to win back` : 'back on the base stake') : 'start with the base stake'}>{d.streak.n ? `${d.streak.n} ${d.streak.kind === 'win' ? 'W' : 'L'}` : '–'}</Tile>
            <Tile label="Most you may stake" sub={a.room !== null ? `${money(a.room)} of loss room left` : 'no daily loss limit'}>{money(a.cap)}</Tile>
          </div>
        </div>
      </section>

      {d.warnings.length > 0 && (
        <div className="mt-3 space-y-2">
          {d.warnings.map((w) => (
            <div key={w.kind} className={`flex gap-2.5 rounded-xl border px-3 py-2.5 text-sm ${w.kind === 'open' ? 'border-line bg-panel text-mut' : 'border-warn/40 bg-warn/10 text-warn'}`} role={w.kind === 'edge' ? 'alert' : undefined}>
              <span aria-hidden="true">{w.kind === 'open' ? '⏳' : '⚠'}</span><span className="min-w-0">{w.text}</span>
            </div>))}
        </div>
      )}

      {/* ---- risk today / why / what next ---- */}
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card title="Risk today" right={<span className="note">{d.day}</span>}>
          <div className="space-y-4">
            <Meter label="Daily loss limit" p={d.limits.loss.pct} value={d.limits.loss.on && d.limits.loss.limit ? `${money(d.limits.loss.used)} of ${money(d.limits.loss.limit)}` : 'off'}
              note={d.limits.loss.on && d.limits.loss.limit ? (d.limits.loss.pct! >= 1 ? 'Reached - stop for today' : `${money(Math.max(0, d.limits.loss.limit - d.limits.loss.used))} left to lose today`) : 'No stop-loss set - consider switching it on'} />
            <Meter label="Daily profit target" p={d.limits.target.pct} color={UP} value={d.limits.target.on && d.limits.target.amount ? `${money(d.limits.target.got)} of ${money(d.limits.target.amount)}` : 'off'}
              note={d.limits.target.on && d.limits.target.amount ? (d.limits.target.pct! >= 1 ? 'Reached - keep the win' : `${money(Math.max(0, d.limits.target.amount - d.limits.target.got))} to go`) : undefined} />
            <Meter label="Losing streak" p={d.limits.streak.on ? Math.min(1, d.limits.streak.n / d.limits.streak.max) : null} ticks={d.limits.streak.on ? d.limits.streak.max : 0}
              value={d.limits.streak.on ? `${d.limits.streak.n} of ${d.limits.streak.max}` : 'off'} note={streakText(d.streak)} />
            <dl className="grid grid-cols-2 gap-2 text-center">
              <div className="rounded-lg border border-line bg-panel2 p-2"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">Drawdown from peak</dt><dd className={`font-semibold tabular-nums ${d.today.drawdown > 0 ? 'text-down' : ''}`}>{money(-d.today.drawdown)}</dd></div>
              <div className="rounded-lg border border-line bg-panel2 p-2"><dt className="text-[10px] font-semibold uppercase tracking-wider text-mut">Staked today</dt><dd className="font-semibold tabular-nums">{money(d.today.staked)}</dd></div>
            </dl>
          </div>
        </Card>

        <Card title="Why this stake">
          {a.reasons.length === 0 ? <Empty>The reasons appear once your balance is known.</Empty> : (
            <ol className="space-y-2.5">
              {a.reasons.map((r, i) => (
                <li key={i} className="flex gap-2.5 text-sm leading-snug"><span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold" style={{ background: `${th.color}26`, color: th.color }} aria-hidden="true">{i + 1}</span><span className="min-w-0 text-fg/90">{r}</span></li>))}
            </ol>
          )}
        </Card>

        <Card title="What happens next" className="md:col-span-2 xl:col-span-1">
          {d.whatif ? (
            <>
              <div className="grid grid-cols-2 gap-2.5">
                <WhatIf title="If it loses" w={d.whatif.loss} money={money} tone="down" />
                <WhatIf title="If it wins" w={d.whatif.win} money={money} tone="up" />
              </div>
              <p className="note mt-3">Worked out at the stake above and a {pct(edge.payout, 0)} payout - the advice you will see after that trade settles.</p>
            </>
          ) : <Empty>{stopped ? 'No further trade is advised today, so there is nothing to compare.' : 'Waiting for your balance.'}</Empty>}
        </Card>
      </div>

      {/* ---- ladder + results | settings ---- */}
      <div className="mt-4 grid gap-4 xl:grid-cols-5">
        <div className="space-y-4 xl:col-span-3">
          <Card title="Last results" right={<span className="note">{d.recent.length ? `last ${d.recent.length}` : ''}</span>}>
            {d.recent.length === 0 ? <Empty>Your finished trades show up here as coloured dots.</Empty> : (
              <div className="flex flex-wrap gap-1.5" role="list" aria-label="Latest results, oldest first">
                {dots(d.recent).map((x) => <span key={x.id} role="listitem" title={x.label} aria-label={x.label} className={`h-4 w-4 rounded-full ${x.cls} sm:h-3.5 sm:w-3.5`} />)}
              </div>
            )}
            <div className="mt-4 rounded-xl border border-line bg-panel2/60 p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-mut">Win rate vs break-even</span>
                <span className="text-sm tabular-nums"><b style={{ color: wr === null ? undefined : wr >= edge.break_even ? UP : DOWN }}>{wr === null ? '–' : pct(wr)}</b> <span className="text-mut">needs {pct(edge.break_even)}</span></span>
              </div>
              <div className="relative mt-2 h-2.5 rounded-full bg-panel" aria-hidden="true">
                <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, (wr ?? 0) * 100)}%`, background: wr !== null && wr >= edge.break_even ? UP : DOWN, opacity: edge.enough ? 1 : 0.45 }} />
                <div className="absolute -top-1 bottom-[-4px] w-0.5 bg-fg" style={{ left: `${edge.break_even * 100}%` }} />
              </div>
              <p className="note mt-2">{edge.enough ? `Over your last ${edge.n} trades at a ${pct(edge.payout, 0)} payout. ${wr !== null && wr < edge.break_even ? 'Below break-even every plan loses slowly: the stake size only changes the speed.' : 'Above break-even: a steady stake lets the edge show.'}`
                : `Needs ${30 - edge.n} more finished trades to judge (${edge.n} so far). Break-even at a ${pct(edge.payout, 0)} payout is ${pct(edge.break_even)}.`}</p>
            </div>
          </Card>

          <Card title="Stake ladder" right={<span className="note">if you lost every trade from a fresh day</span>}>
            {d.ladder.length === 0 ? <Empty>The ladder appears once your balance is known.</Empty> : (
              <>
                <ul className="space-y-1.5">
                  {d.ladder.map((r) => {
                    const stop = r.status === 'stop';
                    const c = stop ? DOWN : r.status === 'careful' ? WARN : UP;
                    const cap = a.cap || 1;
                    return (
                      <li key={r.losses} className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 rounded-lg border border-line bg-panel2/50 px-3 py-2 sm:grid-cols-[6rem_minmax(0,1fr)_7rem_8rem]">
                        <span className="text-xs font-semibold text-mut">{r.losses === 0 ? 'Fresh start' : `After ${r.losses} loss${r.losses === 1 ? '' : 'es'}`}</span>
                        <span className="h-2 overflow-hidden rounded-full bg-panel" aria-hidden="true"><span className="block h-full rounded-full" style={{ width: `${Math.min(100, (r.stake / cap) * 100)}%`, background: c }} /></span>
                        <span className="text-right text-sm font-bold tabular-nums" style={{ color: c }}>{stop ? 'STOP' : money(r.stake)}</span>
                        <span className="col-span-3 text-xs text-mut sm:col-span-1 sm:text-right">
                          {stop ? `${money(-r.lost_before)} lost in total` : r.win_net === null ? '' : r.win_net >= 0 ? <>win → <b className="text-up">{money(r.win_net, true)}</b> overall</> : <>win → <b className="text-warn">{money(r.win_net, true)}</b> overall</>}
                        </span>
                      </li>);
                  })}
                </ul>
                <p className="note mt-2">Bars are drawn against your cap of {money(a.cap)}. A win after losses takes back about 1/{d.settings.recovery.spread} of what is owed plus a normal profit; the streak limit and the daily loss limit always end the ladder.</p>
              </>
            )}
          </Card>
        </div>

        <Card title="Your money rules" className="xl:col-span-2" right={dirty ? <Badge tone="warn">UNSAVED</Badge> : <span className="note">saved</span>}>
          <div className="mb-3">
            <div className="mb-1.5 text-xs font-medium text-mut">Start from</div>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Presets">
              {PRESETS.map((p) => (
                <button key={p.key} type="button" title={p.hint} aria-label={`${p.label} - ${p.hint}`} aria-pressed={presetOf(draft) === p.key} onClick={() => setDraft({ ...draft, ...p.s })}
                  className={`btn btn-sm ${presetOf(draft) === p.key ? 'border-acc bg-acc/15 text-fg' : ''}`}>{p.label}</button>))}
            </div>
          </div>
          <Row label="Normal stake" hint={hints.base !== null ? `= ${money(hints.base)} of your balance now` : 'a share of your balance'}>
            <Num label="Normal stake, percent of balance" value={draft.base_pct} onChange={(v) => patch({ base_pct: v })} suffix="%" min={0.1} max={10} step={0.1} w="w-28" />
          </Row>
          <Row label="Highest stake allowed" hint={hints.max !== null ? `= ${money(hints.max)} - no advice ever goes above it` : 'the hard cap on any single stake'}>
            <Num label="Highest stake allowed, percent of balance" value={draft.max_pct} onChange={(v) => patch({ max_pct: v })} suffix="%" min={0.1} max={10} step={0.1} w="w-28" />
          </Row>
          <Row label="Smallest stake" hint="What Quotex accepts as a minimum">
            <Num label="Smallest stake" value={draft.min_stake} onChange={(v) => patch({ min_stake: v })} prefix={sym} min={0.01} step={0.5} w="w-28" />
          </Row>
          <Row label={<Toggle on={draft.recovery.on} onChange={(v) => nest('recovery', { on: v })} label="Step up a little after a loss" hint="Capped by the highest stake. A win takes back about 1/N of what is owed (N = the number below); the stake steps down as you win." />}>
            <Num label="Wins to recover" value={draft.recovery.spread} onChange={(v) => nest('recovery', { spread: v })} suffix="wins" min={2} max={6} disabled={!draft.recovery.on} w="w-28" />
          </Row>
          <Row label={<Toggle on={draft.loss.on} onChange={(v) => nest('loss', { on: v })} label="Daily loss limit" hint={hints.loss !== null ? `= ${money(hints.loss)} - then stop for the day` : 'then stop for the day'} />}>
            <Num label="Daily loss limit, percent" value={draft.loss.pct} onChange={(v) => nest('loss', { pct: v })} suffix="%" min={0.5} max={50} step={0.5} disabled={!draft.loss.on} w="w-28" />
          </Row>
          <Row label={<Toggle on={draft.target.on} onChange={(v) => nest('target', { on: v })} label="Daily profit target" hint={hints.tgt !== null ? `= ${money(hints.tgt)} - then stop and keep the win` : 'then stop and keep the win'} />}>
            <Num label="Daily profit target, percent" value={draft.target.pct} onChange={(v) => nest('target', { pct: v })} suffix="%" min={0.5} max={100} step={0.5} disabled={!draft.target.on} w="w-28" />
          </Row>
          <Row label={<Toggle on={draft.streak.on} onChange={(v) => nest('streak', { on: v })} label="Stop after a losing streak" hint="Back-to-back losses are when trades get emotional." />}>
            <Num label="Losing streak limit" value={draft.streak.max} onChange={(v) => nest('streak', { max: v })} suffix="in a row" min={2} max={10} disabled={!draft.streak.on} w="w-44" />
          </Row>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <button className="btn btn-primary" disabled={!dirty || put.isPending} onClick={() => { const c = clampSettings(draft); setDraft(c); put.mutate(noAccount(c)); }}>{put.isPending ? 'Saving…' : 'Save rules'}</button>
            <button className="btn" disabled={!dirty} onClick={() => setDraft(saved)}>Discard</button>
            {put.isError && <span className="text-sm text-down">{(put.error as Error).message}</span>}
          </div>
          <p className="note mt-3">The daily limits use your day as set on the <Link to="/guard" className="text-acc underline">Trading guard</Link> page, so both pages talk about the same "today".</p>
        </Card>
      </div>

      <p className="note mt-4">Advice for stake sizing only: Tradzen never places, changes or cancels a trade. No stake plan removes the loss built into trading below break-even ({pct(edge.break_even)} at a {pct(edge.payout, 0)} payout) - it only decides how big each bet is.</p>
    </>
  );
}

/** While typing, keep the cap from dropping below the normal stake so the form never shows an impossible pair. */
function clampLoose(s: MmSettings): MmSettings {
  return s.max_pct < s.base_pct ? { ...s, max_pct: s.base_pct } : s;
}
