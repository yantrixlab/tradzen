import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, qs } from '../api';
import { pct } from '../format';
import { useMoney, usePersisted } from '../hooks';
import { weekdayNames } from '../lib/pnlCalendar';
import { buildMatrix, cellBackground, hourLabel, hourRange, insightsOf, metricOf, scaleOf, MIN_PICK, type HoursResp, type HourStat, type Metric } from '../lib/hourMatrix';
import { ErrorBox, Spinner } from './ui';

const FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const VIEWS: [Metric, string, string][] = [['trades', 'Trades', 'How many trades you opened in each slot'], ['net', 'Profit / loss', 'What each slot made or lost'], ['rate', 'Win rate', 'Your win rate in each slot, against break-even']];
const tone = (x: number | null | undefined) => (x ? (x > 0 ? 'text-emerald-300' : 'text-rose-300') : 'text-slate-300');
const utcLabel = (tz: number) => `UTC${tz < 0 ? '−' : '+'}${String(Math.floor(Math.abs(tz) / 60)).padStart(2, '0')}:${String(Math.abs(tz) % 60).padStart(2, '0')}`;

/** A glass tile with one highlight: the busiest hour, the best, the worst, the best day. */
function Tile({ label, value, sub, accent, empty }: { label: string; value: string; sub: string; accent: 'violet' | 'green' | 'red' | 'amber'; empty?: boolean }) {
  const bar = { violet: 'from-violet-400 to-cyan-300', green: 'from-emerald-400 to-teal-300', red: 'from-rose-400 to-orange-300', amber: 'from-amber-300 to-orange-400' }[accent];
  const text = { violet: 'text-violet-200', green: 'text-emerald-300', red: 'text-rose-300', amber: 'text-amber-200' }[accent];
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-white/[.045] p-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,.1),0_10px_30px_rgba(0,0,0,.25)] backdrop-blur-md">
      <div aria-hidden className={`pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full bg-gradient-to-br ${bar} opacity-20 blur-2xl`} />
      <div className={`mb-2 h-1 w-9 rounded-full bg-gradient-to-r ${bar}`} />
      <div className="text-[10px] font-bold uppercase tracking-[.12em] text-slate-400">{label}</div>
      <div className={`mt-0.5 text-2xl font-extrabold tabular-nums tracking-tight ${empty ? 'text-slate-500' : text}`}>{value}</div>
      <div className="mt-0.5 min-h-[2rem] text-xs leading-snug text-slate-400">{sub}</div>
    </div>
  );
}

/** "When you trade": the finished trades as a heat map of the weekday against the hour they were opened, on your own clock - how many you take, what they made, how often they won -
 *  with the busiest, best and worst hours picked out. Follows the account and the period chosen at the top of the page. */
export function HourlyMatrix({ account, from, to, tz, rangeText, breakEven }: { account: string; from: number | null; to: number | null; tz: number; rangeText: string; breakEven: number | null }) {
  const money = useMoney();
  const [metric, setMetric] = usePersisted<Metric>('trades-hours-metric', 'net');
  const [weekStart] = usePersisted<0 | 1>('trades-cal-weekstart', 1);          // the same choice as the calendar's "Week starts" button
  const [hover, setHover] = useState<{ col: number | null; h: number | null }>({ col: null, h: null });
  const q = useQuery({
    queryKey: ['my-trades-hours', account, from, to, tz],
    queryFn: () => api<HoursResp>(`/api/trades/hours${qs({ account, tz, since: from, until: to })}`),
    refetchInterval: 15000, placeholderData: (p) => p,
  });
  const m = useMemo(() => buildMatrix(q.data?.cells ?? [], weekStart), [q.data, weekStart]);
  const scale = useMemo(() => scaleOf(m, metric), [m, metric]);
  const maxHour = useMemo(() => Math.max(1, ...m.hourTotals.map((s) => s.n)), [m]);
  const ins = useMemo(() => insightsOf(m), [m]);
  const names = weekdayNames(weekStart);
  const dayName = (col: number) => FULL[(weekStart + col) % 7];

  const show = (s: HourStat | null | undefined): string => {
    const v = metricOf(s, metric);
    return v === null ? '' : metric === 'trades' ? String(v) : metric === 'net' ? money(v, true) : `${Math.round(v)}%`;
  };
  const detail = (s: HourStat) => `${s.n} trade${s.n === 1 ? '' : 's'} · ${s.wins} won · ${s.losses} lost${s.draws ? ` · ${s.draws} draw` : ''} · win rate ${pct(s.win_rate, 0)} · staked ${money(s.staked)}`;

  // the readout above the grid keeps one fixed height, so pointing at a slot never moves the grid under the pointer
  const hs: HourStat | null | undefined = hover.h !== null && hover.col !== null ? m.rows[hover.h][hover.col] : hover.h !== null ? m.hourTotals[hover.h] : hover.col !== null ? m.dayTotals[hover.col] : undefined;
  const title = hover.h !== null && hover.col !== null ? `${dayName(hover.col)} · ${hourRange(hover.h)}` : hover.h !== null ? `${hourRange(hover.h)} · every day` : hover.col !== null ? `${dayName(hover.col)} · every hour` : '';

  if (q.isError) return <div className="mt-4"><ErrorBox error={q.error} /></div>;
  const empty = q.data && q.data.summary.n === 0;

  return (
    <section className="relative mt-4 overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#18224a] via-[#0f1631] to-[#0a0f24] p-4 shadow-[0_24px_70px_rgba(0,0,0,.4)] sm:p-6" aria-labelledby="hm-title">
      <div aria-hidden className="pointer-events-none absolute -left-24 -top-28 h-80 w-80 rounded-full bg-violet-500/25 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-36 -right-24 h-96 w-96 rounded-full bg-cyan-400/10 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-white/40 to-transparent" />

      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h2 id="hm-title" className="text-xl font-extrabold tracking-tight text-white">When you trade</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">Your finished trades by the weekday and the hour you opened them · {rangeText} · times on your own clock ({utcLabel(tz)}).</p>
          </div>
          <div className="flex rounded-xl border border-white/10 bg-black/25 p-1 backdrop-blur" role="group" aria-label="What the grid shows">
            {VIEWS.map(([k, label, hint]) => (
              <button key={k} type="button" title={hint} aria-pressed={metric === k} onClick={() => setMetric(k)}
                className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${metric === k ? 'bg-gradient-to-r from-violet-300 to-fuchsia-300 text-[#0a0e1f] shadow-[0_4px_16px_rgba(167,139,250,.45)]' : 'text-slate-300 hover:bg-white/10 hover:text-white'}`}>{label}</button>))}
          </div>
        </div>

        {!q.data ? <div className="py-16"><Spinner /></div> : empty ? (
          <div className="mt-8 rounded-2xl border border-dashed border-white/15 py-14 text-center text-sm text-slate-400">No finished trades in this period yet. Choose a longer period at the top of the page.</div>
        ) : (
          <>
            <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Tile accent="violet" label="Busiest hour" empty={!ins.busiest} value={ins.busiest ? hourLabel(ins.busiest.index) : '–'}
                sub={ins.busiest ? `${ins.busiest.stat.n} trades · ${pct(ins.busiest.share, 0)} of all, every day together` : 'No trades yet'} />
              <Tile accent="green" label="Most profitable hour" empty={!ins.best} value={ins.best ? hourLabel(ins.best.index) : '–'}
                sub={ins.best ? `${money(ins.best.stat.net, true)} · ${ins.best.stat.n} trades · ${pct(ins.best.stat.win_rate, 0)} won` : `No hour with ${MIN_PICK}+ trades made money yet`} />
              <Tile accent="red" label="Costliest hour" empty={!ins.worst} value={ins.worst ? hourLabel(ins.worst.index) : '–'}
                sub={ins.worst ? `${money(ins.worst.stat.net, true)} · ${ins.worst.stat.n} trades · ${pct(ins.worst.stat.win_rate, 0)} won` : `No hour with ${MIN_PICK}+ trades lost money`} />
              <Tile accent="amber" label="Best day" empty={!ins.bestDay} value={ins.bestDay ? dayName(ins.bestDay.index) : '–'}
                sub={ins.bestDay ? `${money(ins.bestDay.stat.net, true)} · ${ins.bestDay.stat.n} trades${ins.worstDay ? ` · worst: ${names[ins.worstDay.index]} ${money(ins.worstDay.stat.net, true)}` : ''}` : `No day with ${MIN_PICK}+ trades made money yet`} />
            </div>

            <div className="mt-4 flex h-[3.6rem] items-center justify-between gap-4 overflow-hidden rounded-2xl border border-white/10 bg-black/25 px-4 backdrop-blur" aria-live="polite">
              {title ? (
                <>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-bold text-white">{title}</div>
                    <div className="line-clamp-2 text-xs text-slate-400">{hs && hs.n ? detail(hs) : 'No trades in this slot'}</div>
                  </div>
                  {hs && hs.n > 0 && <div className="shrink-0 text-right"><div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Net</div><div className={`text-lg font-extrabold tabular-nums ${tone(hs.net)}`}>{money(hs.net, true)}</div></div>}
                </>
              ) : <div className="text-sm text-slate-400">Point at a slot, an hour or a day for its details · {m.total.n} finished trades in this period</div>}
            </div>

            <div className="mt-3 overflow-x-auto rounded-2xl border border-white/10 bg-black/20 p-2 backdrop-blur-sm sm:p-3">
              <table className="w-full min-w-[640px] border-separate text-center [border-spacing:3px]">
                <thead>
                  <tr>
                    <th className="w-14" />
                    {names.map((n, j) => (
                      <th key={n} scope="col" onMouseEnter={() => setHover({ col: j, h: null })} onMouseLeave={() => setHover({ col: null, h: null })}
                        className={`pb-1 text-[11px] font-bold uppercase tracking-wider transition ${hover.col === j ? 'text-white' : 'text-slate-400'}`}>{n}</th>))}
                    <th scope="col" className="w-[17%] pb-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">All days</th>
                  </tr>
                </thead>
                <tbody>
                  {m.rows.map((row, h) => {
                    const tot = m.hourTotals[h];
                    const barBg = tot.net > 0 ? 'linear-gradient(90deg, rgba(34,197,94,.55), rgba(16,185,129,.18))' : tot.net < 0 ? 'linear-gradient(90deg, rgba(239,68,68,.55), rgba(244,63,94,.18))' : 'linear-gradient(90deg, rgba(148,163,184,.4), rgba(148,163,184,.12))';
                    return (
                      <tr key={h}>
                        <th scope="row" onMouseEnter={() => setHover({ col: null, h })} onMouseLeave={() => setHover({ col: null, h: null })}
                          className={`sticky left-0 z-10 bg-[#0d1430]/90 pr-2 text-right text-[11px] font-semibold tabular-nums transition ${hover.h === h ? 'text-white' : 'text-slate-500'}`}>{hourLabel(h)}</th>
                        {row.map((s, j) => (
                          <td key={j} className="p-0">
                            {s ? (
                              <button type="button" aria-label={`${dayName(j)} ${hourRange(h)}: ${detail(s)}, net ${money(s.net, true)}`}
                                onMouseEnter={() => setHover({ col: j, h })} onMouseLeave={() => setHover({ col: null, h: null })} onFocus={() => setHover({ col: j, h })} onBlur={() => setHover({ col: null, h: null })}
                                className="block h-[30px] w-full rounded-lg text-[11px] font-bold tabular-nums text-white shadow-[inset_0_1px_0_rgba(255,255,255,.22)] ring-1 ring-inset ring-white/10 transition hover:z-10 hover:scale-[1.06] hover:ring-2 hover:ring-white/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                                style={{ background: cellBackground(s, metric, scale, breakEven) }}>{show(s)}</button>
                            ) : <div className="h-[30px] rounded-lg bg-white/[.025]" />}
                          </td>))}
                        <td className="p-0" onMouseEnter={() => setHover({ col: null, h })} onMouseLeave={() => setHover({ col: null, h: null })}>
                          {tot.n ? (
                            <div className="relative h-[30px] overflow-hidden rounded-lg bg-white/[.04] ring-1 ring-inset ring-white/10" title={`${hourRange(h)}: ${detail(tot)}`}>
                              <div className="absolute inset-y-0 left-0 rounded-lg" style={{ width: `${Math.max(6, (tot.n / maxHour) * 100)}%`, background: barBg }} />
                              <span className="relative z-10 flex h-full items-center justify-between px-2 text-[11px] font-bold tabular-nums"><span className="text-slate-200">{tot.n}</span><span className="text-white/90">{show(tot)}</span></span>
                            </div>
                          ) : <div className="h-[30px] rounded-lg bg-white/[.025]" />}
                        </td>
                      </tr>);
                  })}
                  <tr>
                    <th scope="row" className="sticky left-0 z-10 bg-[#0d1430]/90 pr-2 pt-1 text-right text-[10px] font-bold uppercase tracking-wider text-slate-400">All hours</th>
                    {m.dayTotals.map((s, j) => (
                      <td key={j} className="p-0 pt-1" onMouseEnter={() => setHover({ col: j, h: null })} onMouseLeave={() => setHover({ col: null, h: null })}>
                        <div className="flex h-[34px] flex-col items-center justify-center rounded-lg border border-white/10 bg-white/[.05] text-[11px] font-bold tabular-nums leading-tight">
                          {s.n ? <><span className={metric === 'net' ? tone(s.net) : 'text-white'}>{show(s)}</span><span className="text-[9px] font-semibold text-slate-500">{s.n} trades</span></> : <span className="text-slate-600">–</span>}
                        </div>
                      </td>))}
                    <td className="p-0 pt-1">
                      <div className="flex h-[34px] flex-col items-center justify-center rounded-lg border border-violet-300/30 bg-gradient-to-br from-violet-400/20 to-cyan-300/10 text-[11px] font-bold tabular-nums leading-tight">
                        <span className={metric === 'net' ? tone(m.total.net) : 'text-white'}>{show(m.total)}</span><span className="text-[9px] font-semibold text-slate-400">{m.total.n} trades</span>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-xs text-slate-400">
              <div className="flex items-center gap-2">
                {metric === 'trades' ? <><span>fewer</span><span aria-hidden className="h-2 w-28 rounded-full bg-gradient-to-r from-violet-500/25 to-cyan-300/80" /><span>more trades</span></>
                  : metric === 'net' ? <><span>loss</span><span aria-hidden className="h-2 w-28 rounded-full bg-gradient-to-r from-rose-500/85 via-slate-400/30 to-emerald-400/85" /><span>profit</span></>
                    : <><span>below break-even</span><span aria-hidden className="h-2 w-28 rounded-full bg-gradient-to-r from-rose-500/85 via-slate-400/30 to-emerald-400/85" /><span>above{breakEven !== null ? ` (${pct(breakEven, 1)})` : ''}</span></>}
              </div>
              <div>The bar in <b className="text-slate-300">All days</b>: how many trades that hour · green = it made money, red = it lost.</div>
            </div>
            <p className="mt-2 text-xs text-slate-500">Slots with fewer than {MIN_PICK} trades are left out of the highlights. A pattern from a few days is mostly chance: look at 30 days or more before you change how you trade.</p>
          </>
        )}
      </div>
    </section>
  );
}
