import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, Candle, qs, Signal, SignalMtg, SignalsMtg } from '../api';
import { dateTime, num, pct, prettyPair, signed, tfName } from '../format';
import { strategyName, useStrategies } from '../hooks';
import { Badge, Card, DirBadge, Drawer, Empty, ErrorBox, PageTitle, ResultBadge, Spinner } from '../components/ui';
import { CandleChart } from '../components/charts';
import { PairMultiSelect, PairOption } from '../components/PairMultiSelect';
import { TrackRecord } from '../components/TrackRecord';
import { RiskChip, RiskReasons } from '../components/RiskChip';

interface Detail {
  signal: Signal; features: Record<string, unknown> | null; candles: Candle[];
  outcome: { entry: number; exit: number; margin_atr: number | null; mfe_atr: number | null; mae_atr: number | null; near_miss: number; result: string } | null;
  notes: { id: number; tags: string[]; note: string; created: number }[];
}
const PAGE = 40;

/** The table's columns: `key` is what the server sorts by (the whole list, not just this page), `first` = the direction of the first click
 *  (text columns A-Z, numbers and times highest / newest first). */
const COLS: { key: string; label: string; first: 'asc' | 'desc'; title: string }[] = [
  { key: 'candle', label: 'Candle', first: 'desc', title: 'Time of the candle the signal is for' },
  { key: 'pair', label: 'Pair', first: 'asc', title: 'Pair name' },
  { key: 'tf', label: 'TF', first: 'asc', title: 'Timeframe' },
  { key: 'strategy', label: 'Strategy', first: 'asc', title: 'Strategy name' },
  { key: 'signal', label: 'Signal', first: 'asc', title: 'CALL / PUT' },
  { key: 'tier', label: 'Tier', first: 'asc', title: 'Tier A / B' },
  { key: 'strength', label: 'Str.', first: 'desc', title: "How many of the strategy's rules agree (0-100). NOT a chance of winning; a one-rule strategy is always 100 and shows a dash. Judge a strategy by its track record." },
  { key: 'adx', label: 'ADX', first: 'desc', title: 'ADX at the signal (trend strength)' },
  { key: 'hour', label: 'Hour', first: 'asc', title: 'Hour of the day (UTC)' },
  { key: 'mode', label: 'Mode', first: 'asc', title: 'live or paper' },
  { key: 'result', label: 'Result', first: 'asc', title: 'win / loss / draw / pending' },
  { key: 'mtg1', label: 'MTG-1', first: 'asc', title: 'What the signal came to with a one-step martingale: won outright, rescued by the re-entry, or lost both (best first)' },
  { key: 'mtg2', label: 'MTG-2', first: 'asc', title: 'What the signal came to with a two-step martingale (best first)' },
  { key: 'margin', label: 'Margin', first: 'desc', title: 'How far the candle closed past the entry, in ATRs (best first)' },
];

/** Columns a phone does not show (tap a row for all of them). */
const SECONDARY = ['tier', 'strength', 'adx', 'hour', 'mode', 'mtg1', 'mtg2', 'margin'];

/** One signal's martingale outcome: "win" = no re-entry needed, "✓ M1" = rescued by the first re-entry, "✗ lost" = every step lost. */
function MtgCell({ m, steps }: { m: SignalMtg | null | undefined; steps: 1 | 2 }) {
  if (!m) return <span className="note">–</span>;
  if (m.skip) return <span className="note" title="Fired while an earlier martingale ladder on this pair was still running: it could not be taken">skipped</span>;
  const net = m.pl == null ? '' : ` Net ${signed(m.pl, 2)} units of the first stake (stake x2.2 per step).`;
  switch (m.outcome) {
    case 'win': return <span className="text-up" title="Won the first trade: no re-entry needed.">win</span>;
    case 'mtg1': return <span className="font-semibold text-up" title={`Lost the first trade, won the first re-entry (next candle, stake x2.2).${net}`}>✓ M1</span>;
    case 'mtg2': return <span className="font-semibold text-up" title={`Lost the first trade and the first re-entry, won the second.${net}`}>✓ M2</span>;
    case 'lost': return <span className="font-semibold text-down" title={`Lost the first trade and all ${steps} re-entr${steps === 1 ? 'y' : 'ies'}.${net}`}>✗ lost</span>;
    case 'draw': return <span className="note" title="Ended on a draw (stake refunded).">draw</span>;
    default: return <span className="text-warn" title="The re-entry candle is not stored yet.">open</span>;
  }
}

/** Accuracy of the settled signals matching the filters: plain, with a one-step and with a two-step martingale. */
function MtgStrip({ s }: { s: SignalsMtg }) {
  const chip = (label: string, acc: number | null, net: number, extra: string, tip: string) => (
    <span className="inline-flex items-baseline gap-1.5 rounded-lg border border-line bg-panel2 px-2.5 py-1" title={tip}>
      <b className="text-mut">{label}</b><b className="tabular-nums">{pct(acc)}</b>
      <span className={`tabular-nums ${net >= 0 ? 'text-up' : 'text-down'}`}>{net >= 0 ? '+' : ''}{Math.round(net).toLocaleString()} u</span>
      {extra && <span className="note">{extra}</span>}
    </span>);
  const tip = (steps: number, m: SignalsMtg['m1']) => `${steps}-step martingale (stake x2.2 per step) on ${m.taken.toLocaleString()} signals: ${m.wins.toLocaleString()} won outright, ${m.rescued.toLocaleString()} rescued by a re-entry, ${m.lost.toLocaleString()} lost every step${m.skipped ? `; ${m.skipped.toLocaleString()} fired mid-ladder and were skipped` : ''}${m.open ? `; ${m.open} still open` : ''}. Net in units of the first stake, against ${m.flat_net.toFixed(1)} without martingale.`;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
      <span className="text-mut">Accuracy of the {s.settled.toLocaleString()} settled signals{s.capped ? ' (newest 20,000)' : ''}:</span>
      {chip('Flat', s.flat_accuracy, s.flat_net, '', 'Every signal as one plain trade (draws left out). Net in units of the stake.')}
      {chip('MTG-1', s.m1.accuracy, s.m1.net, s.m1.rescued ? `${s.m1.rescued.toLocaleString()} rescued` : '', tip(1, s.m1))}
      {chip('MTG-2', s.m2.accuracy, s.m2.net, s.m2.rescued ? `${s.m2.rescued.toLocaleString()} rescued` : '', tip(2, s.m2))}
      <span className="note">A martingale lifts the percentage but a lost ladder costs 3.2 / 8.8 units - read the net.</span>
    </div>);
}

export default function Signals() {
  return (
    <>
      <PageTitle title="Signal history" sub="Every issued signal with its context. Click a row to inspect a win or a loss and tag why it happened." />
      <SignalHistory />
    </>
  );
}

/**
 * The signal table with its filters, sortable columns, martingale columns and signal inspector. On the Signal history page it covers every strategy; given a
 * `strategyId` (the strategy page) it is that strategy's own history: no strategy choice, no strategy column, only the pairs it has signalled.
 */
export function SignalHistory({ strategyId }: { strategyId?: number }) {
  const strategies = useStrategies();
  const cols = strategyId === undefined ? COLS : COLS.filter((c) => c.key !== 'strategy');
  const [f, setF] = useState({ strategy_id: strategyId === undefined ? '' : String(strategyId), tf: '', tier: '', status: '', direction: '', include_skips: false });
  const [pairs, setPairs] = useState<string[]>([]);                                          // picked pairs (none = all)
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null);        // null = the default: newest first
  const set = (k: string, v: string | boolean) => { setF({ ...f, [k]: v }); setOffset(0); };
  const pairOptions = useQuery({ queryKey: ['signal-pairs', strategyId ?? null], queryFn: () => api<PairOption[]>(`/api/signal-pairs${qs({ strategy_id: strategyId })}`), staleTime: 30_000, refetchInterval: 60_000 });
  /** click a heading: its natural direction, then the opposite, then back to the default order */
  const clickSort = (c: (typeof COLS)[number]) => {
    const opposite = c.first === 'asc' ? 'desc' : 'asc';
    setSort(!sort || sort.key !== c.key ? { key: c.key, dir: c.first } : sort.dir === c.first ? { key: c.key, dir: opposite } : null);
    setOffset(0);
  };
  const q = useQuery({
    queryKey: ['signals', f, pairs, offset, sort],
    queryFn: () => api<{ total: number; rows: Signal[]; mtg: SignalsMtg }>(`/api/signals${qs({ ...f, symbol: pairs.join(',') || undefined, include_skips: f.include_skips || undefined, limit: PAGE, offset, sort: sort?.key, order: sort?.dir })}`),
    refetchInterval: 8000,
  });

  return (
    <>
      <Card className="mb-4">
        <div className={`grid grid-cols-2 gap-3 md:grid-cols-4 ${strategyId === undefined ? 'lg:grid-cols-7' : 'lg:grid-cols-6'}`}>
          {strategyId === undefined && <select className="input" value={f.strategy_id} onChange={(e) => set('strategy_id', e.target.value)}>
            <option value="">All strategies</option>{strategies.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>}
          <PairMultiSelect options={pairOptions.data ?? []} value={pairs} onChange={(v) => { setPairs(v); setOffset(0); }} />
          <select className="input" value={f.tf} onChange={(e) => set('tf', e.target.value)}><option value="">Any timeframe</option><option value="60">1m</option><option value="300">5m</option><option value="900">15m</option></select>
          <select className="input" value={f.tier} onChange={(e) => set('tier', e.target.value)}><option value="">Any tier</option><option value="A">Tier A</option><option value="B">Tier B</option></select>
          <select className="input" value={f.status} onChange={(e) => set('status', e.target.value)}><option value="">Any result</option><option value="win">Win</option><option value="loss">Loss</option><option value="draw">Draw</option><option value="pending">Pending</option><option value="void">Void</option></select>
          <select className="input" value={f.direction} onChange={(e) => set('direction', e.target.value)}><option value="">Both directions</option><option value="CALL">CALL</option><option value="PUT">PUT</option></select>
          <label className="flex items-center gap-2 text-sm text-mut"><input type="checkbox" checked={f.include_skips} onChange={(e) => set('include_skips', e.target.checked)} /> include skips</label>
        </div>
      </Card>

      <Card title={strategyId === undefined ? 'Signals' : 'Signal history'} right={q.data && `${q.data.total.toLocaleString()} total`}>
        {q.isError ? <ErrorBox error={q.error} /> : !q.data ? <Spinner /> : q.data.rows.length === 0 ? <Empty>No signals match.</Empty> : (
          <div className="overflow-x-auto">
            {q.data.mtg && q.data.mtg.settled > 0 && <MtgStrip s={q.data.mtg} />}
            <table className="w-full">
              <thead><tr>{cols.map((c) => {
                const on = sort?.key === c.key;
                return (
                  <th key={c.key} className={`th ${SECONDARY.includes(c.key) ? 'max-md:hidden' : ''}`} aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'} title={`${c.title} - click to sort the whole list`}>
                    <button className={`whitespace-nowrap uppercase tracking-wide hover:text-fg ${on ? 'text-[#c4b5fd]' : ''}`} onClick={() => clickSort(c)}>
                      {c.label}{on ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>);
              })}</tr></thead>
              <tbody>
                {q.data.rows.map((r) => (
                  <tr key={r.id} className="cursor-pointer hover:bg-panel2" onClick={() => setOpen(r.id)}>
                    <td className="td tabular-nums">{dateTime(r.target_t)}</td><td className="td">{prettyPair(r.symbol)}</td><td className="td">{tfName(r.tf)}</td>
                    {strategyId === undefined && <td className="td note">{strategyName(strategies.data, r.strategy_id)}</td>}<td className="td"><DirBadge dir={r.direction} /><RiskChip risk={r.risk} /></td>
                    <td className="td max-md:hidden">{r.tier || '–'}</td><td className="td tabular-nums max-md:hidden">{r.factors.length <= 1 ? <span className="note" title="A one-rule strategy has no strength: the rule matched or it did not (it would always read 100). See its track record.">–</span> : r.strength}</td><td className="td tabular-nums max-md:hidden">{num(r.adx, 0)}</td>
                    <td className="td tabular-nums max-md:hidden">{r.hour_utc ?? '–'}</td>
                    <td className="td max-md:hidden">{r.mode === 'paper' ? <Badge tone="acc">paper</Badge> : <Badge tone="up">live</Badge>}</td>
                    <td className="td"><ResultBadge status={r.status} />{r.near_miss ? <span className="ml-1 text-[10px] text-warn" title="closed within 0.25 ATR of entry">near miss</span> : null}</td>
                    <td className="td tabular-nums max-md:hidden"><MtgCell m={r.mtg1} steps={1} /></td><td className="td tabular-nums max-md:hidden"><MtgCell m={r.mtg2} steps={2} /></td>
                    <td className="td tabular-nums note max-md:hidden">{r.margin_atr == null ? '–' : signed(r.margin_atr, 2) + ' ATR'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 flex items-center justify-between">
              <span className="note">{offset + 1}–{Math.min(offset + PAGE, q.data.total)} of {q.data.total}</span>
              <div className="flex gap-2"><button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← {sort ? 'Previous' : 'Newer'}</button>
                <button className="btn btn-sm" disabled={offset + PAGE >= q.data.total} onClick={() => setOffset(offset + PAGE)}>{sort ? 'Next' : 'Older'} →</button></div>
            </div>
          </div>
        )}
      </Card>
      <SignalDrawer id={open} onClose={() => setOpen(null)} strategies={strategies.data} />
    </>
  );
}

const GROUPS: [string, string[]][] = [
  ['Momentum', ['rsi14', 'rsi2', 'stoch_k', 'stoch_d', 'cci20', 'macd_hist']],
  ['Bands & volatility', ['bb_pctb', 'bb_bw', 'bb_bw_pctile', 'atr14', 'atr_ratio']],
  ['Trend', ['adx14', 'plus_di', 'minus_di', 'ema_stack', 'ema21_slope_pct', 'trend_align', 'with_di']],
  ['Candle', ['body_ratio', 'upper_wick_ratio', 'lower_wick_ratio', 'candle_color', 'streak', 'last5', 'dist_hi20_atr', 'dist_lo20_atr']],
  ['Context', ['hour_utc', 'weekday', 'session', 'sentiment', 'payout', 'minutes_since_last', 'concurrent', 'basis_ticks', 'tick_ratio']],
];

function SignalDrawer({ id, onClose, strategies }: { id: number | null; onClose: () => void; strategies: ReturnType<typeof useStrategies>['data'] }) {
  const qc = useQueryClient();
  const d = useQuery({ queryKey: ['signal', id], queryFn: () => api<Detail>(`/api/signals/${id}`), enabled: id !== null });
  const [tags, setTags] = useState('');
  const [note, setNote] = useState('');
  const [wantSimilar, setWantSimilar] = useState(false);
  const similar = useQuery({
    queryKey: ['similar', id], enabled: id !== null && wantSimilar,
    queryFn: () => api<{ neighbours: { t: number; symbol: string; tf: number; result: string; distance: number }[]; summary: { n: number; wins: number; losses: number; acc: number | null } | null; note?: string }>(`/api/signals/${id}/similar`),
  });
  const add = useMutation({
    mutationFn: () => api(`/api/signals/${id}/notes`, { body: { tags: tags.split(',').map((t) => t.trim()).filter(Boolean), note } }),
    onSuccess: () => { setTags(''); setNote(''); qc.invalidateQueries({ queryKey: ['signal', id] }); },
  });
  const del = useMutation({ mutationFn: (nid: number) => api(`/api/notes/${nid}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['signal', id] }) });
  const s = d.data?.signal;

  return (
    <Drawer open={id !== null} onClose={() => { setWantSimilar(false); onClose(); }} width={680}
      title={s ? <span>{prettyPair(s.symbol)} <span className="text-mut">· {tfName(s.tf)} ·</span> <DirBadge dir={s.direction} /></span> : 'Signal'}>
      {d.isError ? <ErrorBox error={d.error} /> : !d.data || !s ? <Spinner /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <ResultBadge status={s.status} /><Badge tone="acc">Tier {s.tier || '–'}</Badge><Badge>{strategyName(strategies, s.strategy_id)} v{s.strategy_version}</Badge>
            <Badge tone={s.mode === 'live' ? 'up' : 'acc'}>{s.mode}</Badge><span className="note">{dateTime(s.target_t)}</span>
            {d.data.outcome?.near_miss ? <Badge tone="warn">near miss</Badge> : null}
          </div>
          <RiskReasons risk={s.risk} />
          <CandleChart candles={d.data.candles} signalT={s.basis_t} targetT={s.target_t} direction={s.direction} />
          {d.data.outcome && (
            <div className="grid grid-cols-4 gap-2 text-center">
              {[['Entry', num(d.data.outcome.entry, 5)], ['Exit', num(d.data.outcome.exit, 5)], ['Close margin', d.data.outcome.margin_atr == null ? '–' : `${signed(d.data.outcome.margin_atr, 2)} ATR`],
                ['Best / worst', `${num(d.data.outcome.mfe_atr, 2)} / ${num(d.data.outcome.mae_atr, 2)}`]].map(([l, v]) => (
                <div key={l} className="rounded-lg bg-panel2 p-2"><div className="label !mb-0">{l}</div><div className="font-semibold tabular-nums">{v}</div></div>))}
            </div>
          )}
          <Card title="Factor votes">
            {s.factors.map((x) => (
              <div key={x.key} className="grid grid-cols-[48px_1fr_auto] items-center gap-2 border-t border-line py-1.5 first:border-0">
                <Badge tone="acc">{Math.round(x.weight)}%</Badge><div><div className="font-medium">{x.name}</div><div className="note">{x.note}</div></div>
                <span className={`font-bold tabular-nums ${x.vote > 0 ? 'text-up' : x.vote < 0 ? 'text-down' : 'text-mut'}`}>{x.vote > 0 ? '+' : ''}{x.vote}</span>
              </div>))}
            <div className="note mt-2">{s.factors.length <= 1 ? 'one rule: it matched (no strength to read)' : `score ${s.score} · strength ${s.strength} · ${s.agree} factors agree`}</div>
          </Card>
          <TrackRecord stats={strategies?.find((x) => x.id === s.strategy_id)?.stats} />
          {d.data.features && (
            <Card title="Context when the signal fired">
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
                {GROUPS.map(([g, keys]) => (
                  <div key={g}><div className="label">{g}</div>
                    {keys.filter((k) => d.data!.features![k] !== undefined && d.data!.features![k] !== null).map((k) => (
                      <div key={k} className="flex justify-between text-xs"><span className="text-mut">{k}</span><span className="tabular-nums">{String(d.data!.features![k])}</span></div>))}
                  </div>))}
              </div>
            </Card>
          )}
          <Card title="Why did it go this way? (notes & tags)">
            {d.data.notes.map((n) => (
              <div key={n.id} className="mb-2 flex items-start justify-between gap-2 rounded-lg bg-panel2 p-2">
                <div><div className="flex flex-wrap gap-1">{n.tags.map((t) => <Badge key={t} tone="acc">{t}</Badge>)}</div><div className="mt-1 text-sm">{n.note}</div></div>
                <button className="btn btn-sm btn-danger" onClick={() => del.mutate(n.id)}>✕</button>
              </div>))}
            <div className="grid gap-2">
              <input className="input" placeholder="tags, comma separated (e.g. news spike, against trend)" value={tags} onChange={(e) => setTags(e.target.value)} />
              <textarea className="input" rows={2} placeholder="what you notice" value={note} onChange={(e) => setNote(e.target.value)} />
              <div><button className="btn btn-primary" disabled={add.isPending || (!tags.trim() && !note.trim())} onClick={() => add.mutate()}>Add note</button></div>
              {add.isError && <ErrorBox error={add.error} />}
            </div>
          </Card>
          <Card title="Similar past signals" right={!wantSimilar && <button className="btn btn-sm" onClick={() => setWantSimilar(true)}>Find</button>}>
            {!wantSimilar ? <div className="note">Nearest neighbours in indicator space and how they ended.</div> : similar.isLoading ? <Spinner /> : similar.data?.summary ? (
              <>
                <div className="mb-2 text-sm">Of the {similar.data.summary.n} most similar settled signals: <b className="text-up">{similar.data.summary.wins} won</b>, <b className="text-down">{similar.data.summary.losses} lost</b>
                  {similar.data.summary.acc != null && <> ({(similar.data.summary.acc * 100).toFixed(0)}%)</>}</div>
                <div className="flex flex-wrap gap-1">{similar.data.neighbours.map((n, i) => <Badge key={i} tone={n.result === 'win' ? 'up' : n.result === 'loss' ? 'down' : 'mut'} title={`${n.symbol} ${dateTime(n.t)}`}>{n.result}</Badge>)}</div>
              </>
            ) : <div className="note">{similar.data?.note ?? 'Not enough data.'}</div>}
          </Card>
        </div>
      )}
    </Drawer>
  );
}
