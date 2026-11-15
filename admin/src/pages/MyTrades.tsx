import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, qs } from '../api';
import { dateTime, pct, prettyPair } from '../format';
import { usePersisted, useMoney } from '../hooks';
import { Badge, Card, DirBadge, Empty, ErrorBox, PageTitle, Spinner, Stat } from '../components/ui';
import TradeDetail from '../components/TradeDetail';
import UndoToasts, { UndoItem } from '../components/UndoToasts';

interface Sig { id: number; tier: string; mode: string; strategy: string; strategy_id: number }
interface Trade {
  id: string; symbol: string | null; direction: 'CALL' | 'PUT' | null; amount: number | null; open_t: number | null; expire_t: number | null; close_t: number | null;
  open_price: number | null; close_price: number | null; payout: number | null; profit: number | null; status: 'open' | 'win' | 'loss' | 'draw'; is_demo: number | null;
  source: 'followed' | 'own' | 'against'; signal: Sig | null; step: 0 | 1; cycle: string;
}
interface Cyc {
  t: number; symbol: string; dir: string; id: string; step_id: string | null; net: number | null; exposure: number; outcome: 'first' | 'recovered' | 'failed' | 'lost' | 'draw' | 'running';
  first: { stake: number; net: number | null; status: string; orders: number }; step: { stake: number; net: number | null; status: string; orders: number; ratio: number | null } | null;
}
interface Mtg {
  positions: number; cycles: number; first: Group; step: Group; taken: number; recovered: number; failed: number; losses_not_followed: number; avg_ratio: number | null;
  net_with_mtg: number; net_without_mtg: number; list: Cyc[];
}
interface Group { n: number; wins: number; losses: number; draws: number; win_rate: number | null; staked: number; net: number; roi: number | null }
interface Resp {
  summary: Group & { open: number; placed: number; avg_stake: number | null; avg_payout: number | null; best: number | null; worst: number | null; break_even: number | null;
    streak: { kind: string | null; n: number }; best_win_streak: number; best_loss_streak: number };
  daily: { date: string; n: number; wins: number; losses: number; net: number; cum: number }[];
  pairs: (Group & { symbol: string })[]; source: (Group & { source: string })[]; sides: (Group & { direction: string })[]; mtg: Mtg;
  total: number; trades: Trade[]; events_seen: number; removed: number;
}
interface Raw { events: { ts: number; dir: string; name: string; payload: unknown }[] }

const tone = (x: number | null | undefined): 'up' | 'down' | undefined => (x ? (x > 0 ? 'up' : 'down') : undefined);
const RANGES: [string, number][] = [['Today', 1], ['7 days', 7], ['30 days', 30], ['All time', 0]];
const SRC: Record<string, [string, string]> = {
  followed: ['Followed signal', 'You entered the same pair and direction shortly after a Tradzen signal.'],
  own: ['Own call', 'No Tradzen signal for that pair around the time you entered.'],
  against: ['Against signal', 'Tradzen had called the other direction when you entered.'],
};

function DailyChart({ days }: { days: Resp['daily'] }) {
  const money = useMoney();
  if (!days.length) return <Empty>No settled trades in this range yet.</Empty>;
  const W = 720, H = 190, pad = 28, n = days.length;
  const max = Math.max(1, ...days.map((d) => Math.max(Math.abs(d.net), Math.abs(d.cum))));
  const y = (v: number) => H / 2 - (v / max) * (H / 2 - 14);
  const bw = Math.min(46, (W - pad * 2) / n - 6);
  const x = (i: number) => pad + ((i + 0.5) * (W - pad * 2)) / n;
  const line = days.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.cum).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full" role="img" aria-label="Profit or loss per day with the running total">
      <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} stroke="currentColor" className="text-line" />
      {days.map((d, i) => (
        <g key={d.date}>
          <rect x={x(i) - bw / 2} width={bw} y={d.net >= 0 ? y(d.net) : H / 2} height={Math.max(1, Math.abs(y(d.net) - H / 2))} rx={2} className={d.net >= 0 ? 'fill-up/70' : 'fill-down/70'}>
            <title>{`${d.date}: ${money(d.net, true)} (${d.wins}W ${d.losses}L)`}</title>
          </rect>
          {(n <= 14 || i % Math.ceil(n / 14) === 0) && <text x={x(i)} y={H + 12} textAnchor="middle" className="fill-mut text-[10px]">{d.date.slice(5)}</text>}
        </g>
      ))}
      <path d={line} fill="none" strokeWidth={2} className="stroke-acc" />
      {days.map((d, i) => <circle key={d.date} cx={x(i)} cy={y(d.cum)} r={3} className="fill-acc"><title>{`Running total ${money(d.cum, true)}`}</title></circle>)}
      <text x={pad} y={10} className="fill-mut text-[10px]">bars: result per day · line: running total</text>
    </svg>
  );
}

const OUTCOME: Record<string, [string, string]> = {
  first: ['up', 'WON FIRST TRY'], recovered: ['acc', 'RECOVERED BY MTG'], failed: ['down', 'MTG LOST TOO'], lost: ['down', 'LOST, NO MTG'], draw: ['mut', 'DRAW'], running: ['warn', 'RUNNING'],
};
const cls = (x: number | null | undefined) => (x ? (x > 0 ? 'text-up' : 'text-down') : '');

function MtgCard({ m, onOpen }: { m: Mtg; onOpen: (id: string) => void }) {
  const money = useMoney();
  if (!m.cycles) return null;
  const lostFirst = m.taken + m.losses_not_followed + (m.list.filter((c) => c.outcome === 'running' && c.first.status === 'loss').length);
  const diff = Math.round((m.net_with_mtg - m.net_without_mtg) * 100) / 100;
  return (
    <Card title="Martingale - one step" className="mt-4">
      <p className="note mb-3">
        Click a cycle to see its chart and both trades. A trade on the same pair opened within 90 seconds after a loss closed counts as the martingale step of that loss. Several clicks on the same pair, side and expiry count as one
        trade (two clicks of 1 = one step of 2). One step at most: if the step loses, the next trade starts a new cycle.
      </p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Cycles" value={m.cycles} sub={`${m.positions} positions · first trades: ${m.first.wins} won, ${m.first.losses} lost${m.first.win_rate !== null ? ` (${pct(m.first.win_rate)})` : ''}`} />
        <Stat label="MTG steps taken" value={m.taken} sub={`${m.recovered} recovered · ${m.failed} lost too${m.step.win_rate !== null ? ` · step win rate ${pct(m.step.win_rate)}` : ''}${m.avg_ratio ? ` · avg ${m.avg_ratio}× stake` : ''}`} />
        <Stat label="Result of the MTG steps" value={money(m.step.net, true)} tone={tone(m.step.net)} sub={`on ${money(m.step.staked)} staked in steps`} />
        <Stat label="With vs without MTG" value={<>{money(m.net_with_mtg, true)} <span className="text-mut">vs</span> {money(m.net_without_mtg, true)}</>} tone={tone(diff)}
          sub={diff === 0 ? 'the steps changed nothing' : `taking the steps ${diff > 0 ? 'added' : 'cost'} ${money(Math.abs(diff))}`} />
      </div>
      {lostFirst > 0 && <p className="note mt-2">Of {lostFirst} lost first trades, you took the step after {m.taken}{m.losses_not_followed ? ` and walked away from ${m.losses_not_followed}` : ''}.</p>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr>{['Opened', 'Pair', 'Side', 'First trade', 'MTG step', 'Cycle result', 'Cycle P / L', 'Most at risk', ''].map((h, i) => <th key={i} className="th">{h}</th>)}</tr></thead>
          <tbody>{m.list.slice(0, 30).map((c) => (
            <tr key={c.t + c.symbol} className="group cursor-pointer transition hover:bg-acc/5 focus-visible:bg-acc/10 focus-visible:outline-none" tabIndex={0} role="button" aria-label={`Open the ${prettyPair(c.symbol)} cycle`}
              onClick={() => onOpen(c.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(c.id); } }}>
              <td className="td whitespace-nowrap" style={{ boxShadow: `inset 3px 0 0 ${c.outcome === 'first' ? '#22c55e' : c.outcome === 'recovered' ? '#8b5cf6' : c.outcome === 'running' ? '#f59e0b' : c.outcome === 'draw' ? '#8a93b2' : '#ef4444'}` }}>{dateTime(c.t)}</td><td className="td whitespace-nowrap font-medium">{prettyPair(c.symbol)}</td><td className="td whitespace-nowrap"><DirBadge dir={c.dir} /></td>
              <td className="td whitespace-nowrap tabular-nums">{money(c.first.stake)} → <span className={cls(c.first.net)}>{c.first.status === 'open' ? 'running' : money(c.first.net, true)}</span></td>
              <td className="td whitespace-nowrap tabular-nums">{c.step ? <>{money(c.step.stake)}{c.step.ratio ? <span className="text-mut"> ({c.step.ratio}×)</span> : null} → <span className={cls(c.step.net)}>{c.step.status === 'open' ? 'running' : money(c.step.net, true)}</span></> : <span className="text-mut">–</span>}</td>
              <td className="td"><Badge tone={OUTCOME[c.outcome][0]}>{OUTCOME[c.outcome][1]}</Badge></td>
              <td className={`td font-semibold tabular-nums ${cls(c.net)}`}>{money(c.net, true)}</td>
              <td className="td tabular-nums">{money(c.exposure)}</td>
              <td className="td w-6 text-mut transition group-hover:translate-x-0.5 group-hover:text-acc" aria-hidden="true">›</td>
            </tr>))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function GroupTable({ rows, label }: { rows: (Group & { key: string; name: string; hint?: string })[]; label: string }) {
  const money = useMoney();
  return (
    <table className="w-full text-sm">
      <thead><tr>{[label, 'Trades', 'Won', 'Win rate', 'Staked', 'Net'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.key}><td className="td font-medium" title={r.hint}>{r.name}</td><td className="td tabular-nums">{r.n}</td>
          <td className="td tabular-nums">{r.wins}<span className="text-mut"> / {r.losses}</span></td><td className="td tabular-nums">{pct(r.win_rate)}</td>
          <td className="td tabular-nums">{money(r.staked)}</td>
          <td className={`td font-semibold tabular-nums ${tone(r.net) === 'up' ? 'text-up' : tone(r.net) === 'down' ? 'text-down' : ''}`}>{money(r.net, true)}</td></tr>))}</tbody>
    </table>
  );
}

export default function MyTrades() {
  const money = useMoney();
  const qc = useQueryClient();
  const [account, setAccount] = usePersisted('trades-account', 'all');
  const [days, setDays] = usePersisted('trades-days', 7);
  const [shown, setShown] = useState(50);
  const [raw, setRaw] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const tz = -new Date().getTimezoneOffset();
  const q = useQuery({ queryKey: ['my-trades', account, days, tz, shown], queryFn: () => api<Resp>(`/api/trades${qs({ account, days, tz, limit: shown })}`), refetchInterval: 3000, placeholderData: (p) => p });
  const rawQ = useQuery({ queryKey: ['my-trades-raw'], queryFn: () => api<Raw>('/api/trades/raw?limit=40'), enabled: raw, refetchInterval: raw ? 4000 : false });
  const refresh = () => qc.invalidateQueries({ queryKey: ['my-trades'] });
  const [toasts, setToasts] = useState<UndoItem[]>([]);
  const dismiss = useCallback((key: string) => setToasts((ts) => ts.filter((x) => x.key !== key)), []);
  const restore = useMutation({ mutationFn: (id: string) => api(`/api/trades/${encodeURIComponent(id)}/restore`, { method: 'POST' }), onSuccess: refresh });
  const restoreAll = useMutation({ mutationFn: () => api('/api/trades/restore', { method: 'POST' }), onSuccess: refresh });
  // removing hides the trade for good (it stays hidden when Quotex resends it) - and offers Undo for 10 seconds
  const del = useMutation({
    mutationFn: (t: Trade) => api(`/api/trades/${encodeURIComponent(t.id)}`, { method: 'DELETE' }),
    onSuccess: (_r, t) => {
      refresh();
      setToasts((ts) => [...ts.filter((x) => x.key !== t.id), { key: t.id, title: 'Trade removed from the list',
        detail: `${t.symbol ? prettyPair(t.symbol) : 'Trade'}${t.direction ? ` · ${t.direction === 'CALL' ? '▲ CALL' : '▼ PUT'}` : ''} · ${dateTime(t.open_t)}`, onUndo: () => restore.mutate(t.id) }].slice(-3));
    },
  });

  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  const d = q.data, s = d.summary;
  const nothing = d.total === 0;
  const settled = s.n;
  const list = d.trades;
  const selIdx = sel ? list.findIndex((t) => t.id === sel) : -1;

  return (
    <>
      <PageTitle title="My trades" sub="The trades you place on Quotex and how they ended. Read from the order messages your Quotex tab already receives - this app never places, changes or cancels a trade." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex gap-1" role="group" aria-label="Account">
          {[['all', 'All accounts'], ['real', 'Real'], ['demo', 'Demo']].map(([k, l]) => (
            <button key={k} className={`btn btn-sm ${account === k ? 'btn-primary' : ''}`} onClick={() => { setAccount(k); setShown(50); }}>{l}</button>))}
        </div>
        <div className="flex gap-1" role="group" aria-label="Period">
          {RANGES.map(([l, v]) => <button key={l} className={`btn btn-sm ${days === v ? 'btn-primary' : ''}`} onClick={() => { setDays(v); setShown(50); }}>{l}</button>)}
        </div>
        <span className="note ml-auto">{s.open ? `${s.open} trade${s.open === 1 ? '' : 's'} running now · ` : ''}updates every few seconds</span>
      </div>

      {nothing && (
        <Card className="mb-4">
          <Empty>
            <div className="text-fg"><b>{d.events_seen ? 'Order messages are arriving, but no trade could be read from them yet.' : 'No trades recorded yet.'}</b></div>
            <div className="mx-auto mt-2 max-w-xl text-left text-sm">
              {d.events_seen
                ? <>The extension is forwarding {d.events_seen} order message{d.events_seen === 1 ? '' : 's'}; open “Raw order messages” at the bottom of this page so the reader can be adjusted to what Quotex really sends.</>
                : <ol className="list-decimal space-y-1 pl-5">
                  <li>Reload the Tradzen extension (<code>chrome://extensions</code> → reload) - this page needs its new version.</li>
                  <li>Reload your Quotex tab, then place a trade as usual.</li>
                  <li>The trade appears here within a second, and its result when it closes. Trades from before this update are not available.</li>
                </ol>}
            </div>
          </Empty>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Net profit / loss" value={money(s.net, true)} tone={tone(s.net)} sub={`on ${money(s.staked)} staked${s.roi !== null ? ` · ${pct(s.roi)} return` : ''}`} />
        <Stat label="Trades placed" value={s.placed} sub={`${settled} finished${s.open ? ` · ${s.open} running` : ''}${s.avg_stake !== null ? ` · avg stake ${money(s.avg_stake)}` : ''}`} />
        <Stat label="Win rate" value={pct(s.win_rate)} tone={s.win_rate !== null && s.break_even !== null ? (s.win_rate > s.break_even ? 'up' : 'down') : undefined}
          sub={`${s.wins} won · ${s.losses} lost${s.draws ? ` · ${s.draws} draw` : ''}${s.break_even !== null ? ` · break-even ${pct(s.break_even)}` : ''}`} />
        <Stat label="Best / worst trade" value={<>{money(s.best, true)} <span className="text-mut">/</span> {money(s.worst, true)}</>}
          sub={s.streak.kind ? `now: ${s.streak.n} ${s.streak.kind} in a row · longest ${s.best_win_streak} won, ${s.best_loss_streak} lost` : undefined} />
      </div>

      {!nothing && s.break_even !== null && s.win_rate !== null && (
        <p className="note mt-2">Against Quotex's payout (about {s.avg_payout}% on a win) you need to win <b>{pct(s.break_even)}</b> of your trades just to break even; you are at <b>{pct(s.win_rate)}</b>{settled < 30 ? ` after only ${settled} finished trades - too few to say anything yet` : ''}.</p>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Profit / loss by day" className="lg:col-span-2"><DailyChart days={d.daily} /></Card>
        <Card title="Did you follow the signals?">
          {settled === 0 ? <Empty>Finished trades will be compared here.</Empty> : <>
            <GroupTable label="Trade" rows={d.source.map((r) => ({ ...r, key: r.source, name: SRC[r.source][0], hint: SRC[r.source][1] }))} />
            <p className="note mt-2">Matched by pair, direction and time against Tradzen' own signals (any strategy, live or paper).</p>
            <div className="mt-3"><GroupTable label="Direction" rows={d.sides.map((r) => ({ ...r, key: r.direction, name: r.direction === 'CALL' ? '▲ CALL' : '▼ PUT' }))} /></div>
          </>}
        </Card>
      </div>

      <MtgCard m={d.mtg} onOpen={setSel} />

      <Card title={`Trades (${d.total})`} className="mt-4" right={<span className="note">Click a trade to see it on the chart</span>}>
          {d.trades.length === 0 ? <Empty>No trades in this range.{d.removed > 0 && <div className="mt-2 text-sm">{d.removed} removed trade{d.removed === 1 ? '' : 's'} hidden · <button className="underline hover:text-fg" onClick={() => restoreAll.mutate()}>Restore {d.removed === 1 ? 'it' : 'all'}</button></div>}</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr>{['Opened', 'Pair', 'Side', 'Stake', 'Price in → out', 'Result', 'P / L', 'Source', '', ''].map((h, i) => <th key={i} className="th">{h}</th>)}</tr></thead>
                <tbody>{d.trades.map((t) => (
                  <tr key={t.id} className={`group cursor-pointer transition hover:bg-acc/5 focus-visible:bg-acc/10 focus-visible:outline-none ${sel === t.id ? 'bg-acc/10' : ''}`} tabIndex={0} role="button"
                    aria-label={`Open the ${t.symbol ? prettyPair(t.symbol) : ''} ${t.direction ?? ''} trade`} onClick={() => setSel(t.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSel(t.id); } }}>
                    <td className="td whitespace-nowrap" style={{ boxShadow: `inset 3px 0 0 ${t.status === 'win' ? '#22c55e' : t.status === 'loss' ? '#ef4444' : t.status === 'open' ? '#f59e0b' : '#8a93b2'}` }}>{dateTime(t.open_t)}{t.is_demo ? <span className="ml-1"><Badge tone="warn">DEMO</Badge></span> : null}</td>
                    <td className="td whitespace-nowrap font-medium">{t.symbol ? prettyPair(t.symbol) : '?'}</td>
                    <td className="td whitespace-nowrap">{t.direction ? <DirBadge dir={t.direction} /> : '–'}{t.step ? <span className="ml-1"><Badge tone="acc" title="Martingale step after a loss on this pair">MTG</Badge></span> : null}</td>
                    <td className="td tabular-nums">{money(t.amount)}</td>
                    <td className="td whitespace-nowrap tabular-nums text-mut">{t.open_price ?? '–'} → {t.close_price ?? '–'}</td>
                    <td className="td"><Badge tone={t.status === 'win' ? 'up' : t.status === 'loss' ? 'down' : t.status === 'open' ? 'warn' : 'mut'}>{t.status === 'open' ? 'RUNNING' : t.status.toUpperCase()}</Badge></td>
                    <td className={`td font-semibold tabular-nums ${tone(t.profit) === 'up' ? 'text-up' : tone(t.profit) === 'down' ? 'text-down' : ''}`}>{money(t.profit, true)}</td>
                    <td className="td whitespace-nowrap" title={SRC[t.source][1]}>
                      {t.source === 'followed' ? <Badge tone="acc">{t.signal ? `${t.signal.strategy} · ${t.signal.tier}` : 'signal'}</Badge> : t.source === 'against' ? <Badge tone="down">against signal</Badge> : <span className="text-mut">own call</span>}
                    </td>
                    <td className="td"><button className="rounded p-1 text-mut hover:bg-down/10 hover:text-down" title="Remove this trade from the list (you can undo)" aria-label="Remove this trade from the list" onClick={(e) => { e.stopPropagation(); del.mutate(t); }}>✕</button></td>
                    <td className="td w-6 text-mut transition group-hover:translate-x-0.5 group-hover:text-acc" aria-hidden="true">›</td>
                  </tr>))}</tbody>
              </table>
              {d.removed > 0 && <p className="note mt-3">{d.removed} removed trade{d.removed === 1 ? '' : 's'} hidden from this list · <button className="underline hover:text-fg" onClick={() => restoreAll.mutate()}>Restore {d.removed === 1 ? 'it' : 'all'}</button></p>}
              {d.total > d.trades.length && <button className="btn btn-sm mt-3" onClick={() => setShown(shown + 100)}>Show 100 more ({d.total - d.trades.length} left)</button>}
            </div>
          )}
        </Card>
        <Card title="By pair" className="mt-4">
          {d.pairs.length === 0 ? <Empty>Nothing finished yet.</Empty> : <GroupTable label="Pair" rows={d.pairs.slice(0, 12).map((r) => ({ ...r, key: r.symbol, name: prettyPair(r.symbol) }))} />}
        </Card>
      <Card title="Raw order messages" className="mt-4" right={<button className="note hover:text-fg" onClick={() => setRaw(!raw)}>{raw ? '▾ Hide' : '▸ Show'}</button>}>
        <p className="note">For troubleshooting only: the last order messages the extension forwarded (only trade fields - no balance, account or login data). {d.events_seen} stored.</p>
        {raw && (rawQ.data
          ? (rawQ.data.events.length === 0 ? <Empty>Nothing received yet.</Empty>
            : <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-panel2 p-3 text-xs">{rawQ.data.events.map((e) => `${new Date(e.ts * 1000).toLocaleTimeString()}  ${e.dir === 'out' ? '→' : '←'} ${e.name}  ${JSON.stringify(e.payload)}`).join('\n')}</pre>)
          : <Spinner />)}
      </Card>

      <UndoToasts items={toasts} onDismiss={dismiss} />

      {sel && (
        <TradeDetail id={sel} onClose={() => setSel(null)} onSelect={setSel} index={Math.max(0, selIdx)} total={list.length}
          onPrev={selIdx > 0 ? () => setSel(list[selIdx - 1].id) : null} onNext={selIdx >= 0 && selIdx < list.length - 1 ? () => setSel(list[selIdx + 1].id) : null} />
      )}
    </>
  );
}
