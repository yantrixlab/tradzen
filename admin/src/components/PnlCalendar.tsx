import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, qs } from '../api';
import { pct, prettyPair } from '../format';
import { useMoney, usePersisted } from '../hooks';
import { fromYmd, ymd } from '../lib/dateRanges';
import { addMonths, dayKey, longDate, monthLabel, monthOf, monthRange, monthsBack, monthsSpan, sameMonth, shade, weekTotal, weekdayNames, weeksOf, type CalResp, type DayStat, type MonthKey } from '../lib/pnlCalendar';
import { Card, Empty, Spinner } from './ui';

const UP = 'text-up', DOWN = 'text-down';
const toneOf = (x: number | null | undefined) => (x ? (x > 0 ? UP : DOWN) : '');
const MODES: [string, string][] = [['month', 'This month'], ['last', 'Last month'], ['3m', '3 months'], ['6m', '6 months'], ['all', 'Lifetime'], ['custom', 'Custom']];
const DAY_MS = 86400;

/** One month as a grid of days (+ a weekly total), coloured by the day's result. */
function MonthGrid({ k, days, maxAbs, weekStart, today, small, picked, onHover, onPick }: {
  k: MonthKey; days: Record<string, DayStat>; maxAbs: number; weekStart: 0 | 1; today: string; small: boolean; picked: string | null;
  onHover: (d: string | null) => void; onPick: (d: string) => void;
}) {
  const money = useMoney();
  const weeks = weeksOf(k, weekStart);
  const names = weekdayNames(weekStart);
  const cols = 'grid grid-cols-7 gap-1 sm:grid-cols-[repeat(7,minmax(0,1fr))_minmax(0,1.15fr)] sm:gap-1.5';
  return (
    <div>
      <div className="mb-1.5 text-center text-sm font-semibold">{monthLabel(k)}</div>
      <div className={`${cols} mb-1 text-center text-[10px] font-semibold uppercase tracking-wider text-mut`}>{names.map((n) => <div key={n}>{n.slice(0, small ? 1 : 3)}</div>)}<div className="hidden sm:block">Week</div></div>
      <div className="space-y-1 sm:space-y-1.5">{weeks.map((w, i) => {
        const tot = weekTotal(w, days);
        return (
          <div key={i} className={cols}>
            {w.map((d, j) => {
              if (!d) return <div key={j} aria-hidden />;
              const s = days[d];
              const num = Number(d.slice(8));
              const future = d > today;
              const style = s ? { background: shade(s.net, maxAbs) } : undefined;
              const label = s ? `${longDate(d)}: ${money(s.net, true)}, ${s.n} trade${s.n === 1 ? '' : 's'}` : `${longDate(d)}: no trades`;
              return (
                <button key={j} type="button" aria-label={label} aria-pressed={picked === d} disabled={!s}
                  onMouseEnter={() => s && onHover(d)} onMouseLeave={() => onHover(null)} onFocus={() => s && onHover(d)} onBlur={() => onHover(null)} onClick={() => s && onPick(d)}
                  className={`relative flex min-w-0 flex-col items-center justify-center rounded-lg px-0.5 text-center transition ${small ? 'min-h-[40px] sm:min-h-[48px]' : 'min-h-[52px] sm:min-h-[68px]'} ${s ? 'cursor-pointer hover:brightness-125' : 'cursor-default bg-panel2/40'} ${future ? 'opacity-40' : ''} ${d === today ? 'ring-1 ring-acc' : ''} ${picked === d ? 'outline outline-2 outline-offset-1 outline-fg' : ''}`}
                  style={style}>
                  <span className={`absolute left-1.5 top-0.5 text-[10px] ${s ? 'text-fg/70' : 'text-mut/70'}`}>{num}</span>
                  {s && <>
                    <span className={`max-w-full font-bold tabular-nums tracking-tight ${small ? 'text-[9px] sm:text-[11px]' : 'text-[10px] sm:text-sm'} text-fg`}>{money(s.net, true)}</span>
                    {!small && <span className="hidden text-[10px] text-fg/70 sm:block">{s.n} trade{s.n === 1 ? '' : 's'}</span>}
                  </>}
                </button>);
            })}
            <div className="hidden min-w-0 flex-col items-center justify-center rounded-lg border border-line/60 px-1 text-center sm:flex">
              {tot.days ? <><span className={`max-w-full truncate text-xs font-semibold tabular-nums ${toneOf(tot.net)}`}>{money(tot.net, true)}</span><span className="text-[10px] text-mut">{tot.n} trade{tot.n === 1 ? '' : 's'}</span></> : <span className="text-[10px] text-mut/60">–</span>}
            </div>
          </div>);
      })}</div>
    </div>
  );
}

/** The profit / loss calendar: a coloured month per day with a weekly total, the period's summary, a detail line for the day under the pointer, and the pairs that earned most
 *  and least beside it. Click a day to list its trades below. Ranges and "days" follow the viewer's own midnights. */
export function PnlCalendar({ account, tz, onShowDay }: { account: string; tz: number; onShowDay: (day: string) => void }) {
  const money = useMoney();
  const [mode, setMode] = usePersisted('trades-cal-mode', 'month');
  const [custom, setCustom] = usePersisted('trades-cal-custom', { from: ymd(new Date(Date.now() - 29 * DAY_MS * 1000)), to: ymd(new Date()) });
  const [weekStart, setWeekStart] = usePersisted<0 | 1>('trades-cal-weekstart', 1);
  const [cursor, setCursor] = useState<MonthKey>(() => monthOf(new Date()));
  const [hover, setHover] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const now = new Date();
  const curMonth = monthOf(now);
  const today = dayKey(now.getFullYear(), now.getMonth(), now.getDate());

  const plan = useMemo<{ months: MonthKey[] | null; range: { from?: number; to?: number } }>(() => {
    const n = new Date();
    if (mode === 'month') return { months: [cursor], range: monthRange(cursor) };
    if (mode === 'last') { const k = addMonths(monthOf(n), -1); return { months: [k], range: monthRange(k) }; }
    if (mode === '3m' || mode === '6m') { const ms = monthsBack(n, mode === '3m' ? 3 : 6); return { months: ms, range: { from: monthRange(ms[ms.length - 1]).from, to: monthRange(ms[0]).to } }; }
    if (mode === 'custom') {
      const a = fromYmd(custom.from), b = fromYmd(custom.to);
      const [lo, hi] = a && b && a > b ? [b, a] : [a, b];
      if (!lo || !hi) return { months: [monthOf(n)], range: {} };
      return { months: monthsSpan(ymd(lo), ymd(hi), 24), range: { from: Math.floor(lo.getTime() / 1000), to: Math.floor(new Date(hi.getFullYear(), hi.getMonth(), hi.getDate() + 1).getTime() / 1000) } };
    }
    return { months: null, range: {} };          // lifetime: the months come from the data
  }, [mode, cursor, custom]);

  const q = useQuery({
    queryKey: ['pnl-calendar', account, tz, plan.range.from ?? 0, plan.range.to ?? 0],
    queryFn: () => api<CalResp>(`/api/trades/calendar${qs({ account, tz, since: plan.range.from, until: plan.range.to })}`),
    refetchInterval: 15000, placeholderData: (p) => p,
  });
  const d = q.data;
  const months: MonthKey[] = plan.months ?? (d?.summary.first_day && d.summary.last_day ? monthsSpan(d.summary.first_day, d.summary.last_day, 60) : [curMonth]);
  const limit = mode === 'all' ? 12 : 24;
  const shown = showAll ? months : months.slice(0, limit);
  const single = months.length === 1;
  const maxAbs = useMemo(() => Math.max(0, ...Object.values(d?.days ?? {}).map((s) => Math.abs(s.net))), [d]);
  const s = d?.summary;
  const focus = hover ?? picked;
  const fd = focus && d ? d.days[focus] : null;

  const setPill = (k: string) => { setMode(k); setPicked(null); setHover(null); setShowAll(false); if (k === 'month') setCursor(curMonth); };
  const pairs = d?.pairs ?? [];
  const best = pairs.filter((p) => p.net > 0).slice(0, 6);
  const worst = [...pairs].filter((p) => p.net < 0).sort((a, b) => a.net - b.net).slice(0, 3);
  const maxPair = Math.max(0.01, ...pairs.map((p) => Math.abs(p.net)));

  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-3">
      <Card title="Profit / loss calendar" className="lg:col-span-2"
        right={<div className="flex items-center gap-2"><button className="btn btn-sm" onClick={() => setWeekStart(weekStart === 1 ? 0 : 1)} title="Which day the week starts on">Week starts {weekStart === 1 ? 'Mon' : 'Sun'}</button></div>}>
        <div className="-mx-1 mb-3 flex max-w-full gap-1 overflow-x-auto px-1 pb-1" role="group" aria-label="Calendar period">
          {MODES.map(([k, l]) => <button key={k} className={`btn btn-sm shrink-0 whitespace-nowrap ${mode === k ? 'btn-on' : ''}`} aria-pressed={mode === k} onClick={() => setPill(k)}>{l}</button>)}
        </div>
        {mode === 'custom' && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <input type="date" className="input !w-auto" aria-label="Calendar from date" value={custom.from} max={custom.to || undefined} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
            <span className="text-mut">to</span>
            <input type="date" className="input !w-auto" aria-label="Calendar to date" value={custom.to} min={custom.from || undefined} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
          </div>)}

        <div className="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
          {/* A FIXED height, whatever it shows: a block that grows when a day is hovered pushes the whole calendar down, the pointer is then off the day, the block shrinks, the calendar
              moves back under the pointer ... and the screen flickers up and down. The text is clamped to the room (its full wording is in the tooltip). */}
          <div className="h-[5.25rem] min-w-0 flex-[1_1_14rem] overflow-hidden text-sm sm:h-[4.25rem]">
            {fd && focus ? (() => {
              const detail = `${fd.n} trade${fd.n === 1 ? '' : 's'} · ${fd.wins} won · ${fd.losses} lost${fd.draws ? ` · ${fd.draws} draw` : ''} · win rate ${pct(fd.win_rate, 0)} · staked ${money(fd.staked)}`
                + `${fd.best !== null ? ` · best ${money(fd.best, true)} · worst ${money(fd.worst, true)}` : ''}${fd.top_pair ? ` · top pair ${prettyPair(fd.top_pair.symbol)} (${money(fd.top_pair.net, true)})` : ''}`;
              return (
                <>
                  <div className="truncate font-semibold">{longDate(focus)}</div>
                  <div className="note line-clamp-4 sm:line-clamp-3" title={detail}>{detail}</div>
                </>);
            })() : <div className="note">{single ? monthLabel(months[0]) : 'Period'} · hover or tap a day for its details</div>}
          </div>
          <div className="text-right">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-mut">{fd && focus ? 'Day result' : 'Period result'}</div>
            <div className={`text-xl font-extrabold tabular-nums sm:text-2xl ${toneOf(fd ? fd.net : s?.net)}`}>{fd ? money(fd.net, true) : money(s?.net ?? 0, true)}</div>
          </div>
        </div>

        {single && (
          <div className="mb-3 flex items-center justify-between gap-2">
            <button className="btn btn-sm" aria-label="Previous month" onClick={() => { const k = addMonths(months[0], -1); setMode('month'); setCursor(k); setPicked(null); }}>◀</button>
            <span className="note">{sameMonth(months[0], curMonth) ? 'current month' : ''}</span>
            <button className="btn btn-sm" aria-label="Next month" disabled={sameMonth(months[0], curMonth)} onClick={() => { setMode('month'); setCursor(addMonths(months[0], 1)); setPicked(null); }}>▶</button>
          </div>)}

        {!d ? <Spinner /> : (
          <div className={`grid gap-4 ${single ? '' : 'xl:grid-cols-2'}`}>
            {shown.map((k) => <MonthGrid key={`${k.y}-${k.m}`} k={k} days={d.days} maxAbs={maxAbs} weekStart={weekStart} today={today} small={!single} picked={picked}
              onHover={setHover} onPick={(day) => { setPicked(day); onShowDay(day); }} />)}
          </div>)}
        {months.length > limit && <div className="mt-3 text-center"><button className="btn btn-sm" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer months' : `Show all ${months.length} months`}</button></div>}
        {d && s && s.trading_days === 0 && <div className="mt-3"><Empty>No finished trades in this period.</Empty></div>}

        {s && s.trading_days > 0 && (
          <div className="mt-4 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {[
              ['Trading days', `${s.trading_days}`, `${s.green_days} green · ${s.red_days} red${s.flat_days ? ` · ${s.flat_days} flat` : ''}`],
              ['Win rate', pct(s.win_rate, 1), `${s.wins} won · ${s.losses} lost${s.draws ? ` · ${s.draws} draw` : ''}`],
              ['Best day', s.best_day ? money(s.best_day.net, true) : '–', s.best_day ? longDate(s.best_day.date).replace(/, \d{4}$/, '') : ''],
              ['Worst day', s.worst_day ? money(s.worst_day.net, true) : '–', s.worst_day ? longDate(s.worst_day.date).replace(/, \d{4}$/, '') : ''],
              ['Average day', s.avg_day !== null ? money(s.avg_day, true) : '–', `${s.n} trades · staked ${money(s.staked)}`],
              ['Return on staked', s.roi !== null ? pct(s.roi, 1) : '–', ''],
              ['Longest green run', `${s.best_green_streak} day${s.best_green_streak === 1 ? '' : 's'}`, 'days you traded'],
              ['Longest red run', `${s.best_red_streak} day${s.best_red_streak === 1 ? '' : 's'}`, s.current_streak.kind ? `now: ${s.current_streak.n} ${s.current_streak.kind} in a row` : ''],
            ].map(([t, v, sub]) => (
              <div key={t} className="rounded-lg border border-line/70 bg-panel2/50 p-2"><div className="text-[10px] font-semibold uppercase tracking-wider text-mut">{t}</div><div className="text-sm font-bold tabular-nums">{v}</div><div className="truncate text-mut">{sub}</div></div>))}
          </div>)}
      </Card>

      <Card title="Pairs in this period">
        {!d ? <Spinner /> : pairs.length === 0 ? <Empty>No finished trades in this period.</Empty> : (
          <div className="space-y-4">
            <PairList title="Best pairs" rows={best} max={maxPair} empty="No pair made money in this period." />
            {worst.length > 0 && <PairList title="Weakest pairs" rows={worst} max={maxPair} />}
            <p className="note">Ranked by net result over the period shown in the calendar. A pair with only a few trades says little.</p>
          </div>)}
      </Card>
    </div>
  );
}

function PairList({ title, rows, max, empty }: { title: string; rows: CalResp['pairs']; max: number; empty?: string }) {
  const money = useMoney();
  return (
    <div>
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-mut">{title}</div>
      {rows.length === 0 ? <div className="note">{empty}</div> : (
        <ul className="space-y-2">{rows.map((p) => (
          <li key={p.symbol}>
            <div className="flex items-baseline justify-between gap-2 text-sm"><span className="min-w-0 truncate font-medium">{prettyPair(p.symbol)}</span><span className={`shrink-0 font-semibold tabular-nums ${toneOf(p.net)}`}>{money(p.net, true)}</span></div>
            <div className="mt-0.5 flex items-center gap-2"><div className="h-1.5 flex-1 rounded bg-line"><div className={`h-full rounded ${p.net >= 0 ? 'bg-up' : 'bg-down'}`} style={{ width: `${Math.max(4, (Math.abs(p.net) / max) * 100)}%` }} /></div>
              <span className="note shrink-0 tabular-nums">{p.n} trade{p.n === 1 ? '' : 's'} · {pct(p.win_rate, 0)}</span></div>
          </li>))}</ul>)}
    </div>
  );
}
