import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api, Strategy } from '../api';
import { ago, pct, signed, verdict } from '../format';
import { useMe, usePersisted, useStrategies } from '../hooks';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner, VerdictBadge } from '../components/ui';

export function StatusSelect({ s }: { s: Strategy }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: (status: string) => api(`/api/strategies/${s.id}/status`, { body: { status } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['strategy', String(s.id)] }); },
  });
  const tone = s.status === 'live' ? 'text-up' : s.status === 'paper' ? 'text-[#c4b5fd]' : s.status === 'paused' ? 'text-warn' : 'text-mut';
  return (
    <span className="inline-flex items-center gap-1">
      <select className={`input !w-auto !py-1 !text-xs font-bold uppercase ${tone}`} value={s.status} disabled={m.isPending} onChange={(e) => m.mutate(e.target.value)} title={m.isError ? String(m.error) : ''}>
        {['draft', 'paper', 'live', 'paused', 'archived'].map((x) => <option key={x} value={x}>{x}</option>)}
      </select>
      {s.muted && <Badge tone="down" title="the last 100 settled signals are clearly below break-even">MUTED</Badge>}
    </span>
  );
}

const AUDIENCE: [string, string][] = [['admin', 'Testing (staff only)'], ['optin', 'Library (users add it)'], ['everyone', 'Everybody']];

/** Who gets the signals of a shared strategy (staff only): testing -> published in the library -> sent to every user. */
export function AudienceSelect({ s }: { s: Strategy }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: (audience: string) => api(`/api/strategies/${s.id}/audience`, { body: { audience } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['strategy', String(s.id)] }); },
  });
  const a = s.audience ?? 'admin';
  return (
    <div className="mt-1">
      <select aria-label={`Who gets ${s.name}`} className={`input !w-auto !py-0.5 !text-xs ${a === 'admin' ? 'text-mut' : 'text-[#c4b5fd]'}`} value={a} disabled={m.isPending} onChange={(e) => m.mutate(e.target.value)}
        title={m.isError ? String(m.error) : 'Who receives the signals of this strategy once it is live'}>
        {AUDIENCE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

type SortKey = 'name' | 'mode' | 'version' | 'signals' | 'wl' | 'accuracy' | 'mtg' | 'verdict' | 'exp' | 'last';
interface Sort { key: SortKey; dir: 1 | -1 }

const MODE_RANK: Record<string, number> = { live: 0, paper: 1, paused: 2, draft: 3, archived: 4 };
const VERDICT_RANK: Record<string, number> = { above: 4, near: 3, sample: 2, none: 1, below: 0 };

/** Column titles you can click. `first` = the direction of the first click (1 = A-Z / live first, -1 = biggest / newest first). */
const COLS: { key: SortKey; label: string; first: 1 | -1; title?: string }[] = [
  { key: 'name', label: 'Strategy', first: 1 },
  { key: 'mode', label: 'Mode', first: 1, title: 'Live first, then paper, paused, draft, archived' },
  { key: 'version', label: 'v', first: -1 },
  { key: 'signals', label: 'Signals', first: -1 },
  { key: 'wl', label: 'W / L', first: -1, title: 'Sorts by wins' },
  { key: 'accuracy', label: 'Accuracy', first: -1 },
  { key: 'mtg', label: 'MTG-1 acc.', first: -1,
    title: "Win rate of this strategy's real settled signals if every loss got ONE re-entry on the next candle (stake x2.2). The net result under it is what that would really have done to the account - a high percentage can still lose money." },
  { key: 'verdict', label: 'Verdict', first: -1, title: 'Best verdict first: above, near, small sample, below break-even' },
  { key: 'exp', label: 'Exp./trade', first: -1 },
  { key: 'last', label: 'Last signal', first: -1 },
];

const sortValue = (s: Strategy, key: SortKey): number | string | null => {
  switch (key) {
    case 'name': return s.name.toLowerCase();
    case 'mode': return MODE_RANK[s.status] ?? 9;
    case 'version': return s.version;
    case 'signals': return s.stats.total;
    case 'wl': return s.stats.wins;
    case 'accuracy': return s.stats.accuracy;
    case 'mtg': return s.mtg1?.accuracy ?? null;
    case 'verdict': return VERDICT_RANK[verdict(s.stats.settled, s.stats.ci_low, s.stats.ci_high, s.stats.breakeven)] ?? 1;
    case 'exp': return s.stats.expectancy;
    case 'last': return s.last_signal;
  }
};

/** Default order: live strategies first, then the highest accuracy (strategies with nothing settled yet last), then the most signals, then A-Z. */
const defaultOrder = (a: Strategy, b: Strategy): number => {
  const live = Number(b.status === 'live') - Number(a.status === 'live');
  if (live) return live;
  const x = a.stats.accuracy, y = b.stats.accuracy;
  if (x !== y) return x === null ? 1 : y === null ? -1 : y - x;
  return b.stats.settled - a.stats.settled || a.name.localeCompare(b.name);
};

function compare(a: Strategy, b: Strategy, sort: Sort): number {
  const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
  if (x === null && y === null) return defaultOrder(a, b);
  if (x === null) return 1;                                                   // empty values always sink to the bottom, whichever way you sort
  if (y === null) return -1;
  const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number);
  return c * sort.dir || defaultOrder(a, b);
}

/** One-step martingale on this strategy's REAL settled signals: win rate with a re-entry, and what it did to the account. */
function MtgCell({ s }: { s: Strategy }) {
  const m = s.mtg1;
  if (!m) return <td className="td note" title={s.mtg1_pending ? 'Calculating…' : 'No settled signals yet'}>{s.mtg1_pending ? '…' : '–'}</td>;
  const tip = `${m.taken.toLocaleString()} signals taken${m.skipped ? ` (${m.skipped.toLocaleString()} fired mid-ladder and were skipped)` : ''}: ${m.wins.toLocaleString()} won outright, ${m.rescued.toLocaleString()} rescued by the re-entry, ${m.lost.toLocaleString()} lost both. Net with MTG ${m.net.toFixed(1)} units vs ${m.flat_net.toFixed(1)} flat (stake x${m.mult}).`;
  return (
    <td className="td tabular-nums" title={tip}>{pct(m.accuracy)}
      <div className={`note ${m.net < m.flat_net ? 'text-down' : 'text-up'}`}>net {m.net >= 0 ? '+' : ''}{Math.round(m.net).toLocaleString()} u · {m.rescued} rescued</div></td>
  );
}

export default function Strategies() {
  const q = useStrategies();
  const nav = useNavigate();
  const staff = useMe().data?.user.role !== 'trader';
  const [show, setShow] = useState<'active' | 'all'>('active');
  const [filter, setFilter] = useState('');
  const [sort, setSort] = usePersisted<Sort | null>('strategies.sort', null);
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const rows = q.data
    .filter((s) => (show === 'all' || s.status !== 'archived') && (s.name + s.key + s.tags.join(' ')).toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => (sort ? compare(a, b, sort) : defaultOrder(a, b)));
  const clickSort = (key: SortKey, first: 1 | -1) => setSort(sort && sort.key === key ? { key, dir: sort.dir === 1 ? -1 : 1 } : { key, dir: first });
  const active = sort ? COLS.find((c) => c.key === sort.key) : null;

  return (
    <>
      <PageTitle title="Strategies" sub="Every strategy runs on the same candles with the same rules. Paper = recorded and settled, no alerts. Live = alerts on."
        right={<><input className="input !w-56" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <label className="flex items-center gap-1.5 text-sm text-mut"><input type="checkbox" checked={show === 'all'} onChange={(e) => setShow(e.target.checked ? 'all' : 'active')} />archived</label>
          <button className="btn btn-primary" onClick={() => nav('/strategies/new')}>+ New strategy</button></>} />
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-mut">
        {active && sort
          ? <><span>Sorted by <b className="text-fg">{active.label}</b> {sort.dir === 1 ? '▲' : '▼'}</span><button className="btn btn-sm" onClick={() => setSort(null)}>Back to default order</button></>
          : <span>Default order: <b className="text-fg">live strategies first, then highest accuracy</b>. Click any column title to sort by it; click again to reverse.</span>}
      </div>
      <Card>
        {rows.length === 0 ? <Empty>No strategies match.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead><tr>{COLS.map((c) => (
                <th key={c.key} className="th" aria-sort={sort?.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'} title={c.title}>
                  <button className={`whitespace-nowrap uppercase tracking-wide hover:text-fg ${sort?.key === c.key ? 'text-[#c4b5fd]' : ''}`} onClick={() => clickSort(c.key, c.first)}>
                    {c.label}{sort?.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}</button></th>))}<th className="th" /></tr></thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id}>
                    <td className="td">
                      <Link to={`/strategies/${s.id}`} className="font-semibold hover:text-acc">{s.name}</Link>
                      {s.error && <Badge tone="down" title={s.error.join('; ')}>ERROR</Badge>}
                      <div className="note">{s.kind === 'builtin' ? 'built-in' : s.mine ? 'my strategy' : 'custom'}{s.tags.length ? ' · ' + s.tags.join(' · ') : ''}</div>
                    </td>
                    <td className="td"><StatusSelect s={s} />{staff && s.owner_id == null && <AudienceSelect s={s} />}</td>
                    <td className="td tabular-nums">{s.version}</td>
                    <td className="td tabular-nums">{s.stats.total}</td>
                    <td className="td tabular-nums">{s.stats.wins} / {s.stats.losses}</td>
                    <td className="td tabular-nums">{pct(s.stats.accuracy)}{s.stats.settled > 0 && <span className="note"> ({pct(s.stats.ci_low, 0)}–{pct(s.stats.ci_high, 0)})</span>}</td>
                    <MtgCell s={s} />
                    <td className="td"><VerdictBadge settled={s.stats.settled} lo={s.stats.ci_low} hi={s.stats.ci_high} be={s.stats.breakeven} /></td>
                    <td className="td tabular-nums">{signed(s.stats.expectancy)}</td>
                    <td className="td note">{ago(s.last_signal)}</td>
                    <td className="td"><div className="flex gap-1">
                      <Link className="btn btn-sm" to={`/backtests?strategy=${s.id}`}>Backtest</Link>
                      <Link className="btn btn-sm" to={`/strategies/${s.id}/clone`}>Clone</Link></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="note mt-3">Verdict compares the 95 % accuracy interval with the break-even rate implied by the payout. "Small sample" until 30 settled signals; real conclusions need a few hundred.
        MTG-1 uses each strategy's real settled signals: one re-entry on the next candle after every loss (stake ×2.2); the net figure under it is what that did to the account.</p>
    </>
  );
}
