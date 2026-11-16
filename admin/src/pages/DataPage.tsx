import { Fragment, useState } from 'react';
import { ago, dateTime, pct, prettyPair, tfName } from '../format';
import { useCoverage, useGaps, useLiveState, useMe, usePersisted, usePlan } from '../hooks';
import { Badge, Card, Empty, Field, PageTitle, Spinner, Stat, Tabs } from '../components/ui';
import { ContribPanel } from '../components/ContribPanel';
import { FeedPanel } from '../components/FeedPanel';
import { SentimentPanel } from '../components/SentimentPanel';

function PlanTab({ now }: { now: number }) {
  const [minPayout, setMinPayout] = usePersisted('plan.minPayout', 80);
  const [target, setTarget] = usePersisted('plan.target', 100000);
  const [perPair, setPerPair] = usePersisted('plan.perPair', 5000);
  const plan = usePlan(minPayout, target, perPair);
  const p = plan.data;
  const days = (h: number) => (h > 48 ? `${(h / 24).toFixed(0)} days` : `${h.toFixed(0)} hours`);
  const todo = (x: NonNullable<typeof p>['pairs'][number]) => {
    if (x.done) return <span className="text-up">✓ done</span>;
    if (!x.open) return <span className="note">market closed on Quotex now - skip</span>;
    const when = x.scroll_to ? `to ${dateTime(x.scroll_to)}` : `about ${(perPair / 1440).toFixed(1)} days`;
    return <span>{x.streaming ? <Badge tone="up">● live now</Badge> : <Badge>not open</Badge>} {x.streaming ? 'Scroll its chart back' : 'Open it on Quotex, then scroll its chart back'} {when}</span>;
  };
  return (
    <div className="space-y-4">
      <Card title="Collection plan" right={<span className="note normal-case tracking-normal">progress counts 1m candles on pairs paying ≥ {minPayout}% only - low-payout pairs can't be profitable</span>}>
        {!p ? <Spinner /> : (
          <div className="space-y-4">
            <div>
              <div className="mb-1 flex items-baseline justify-between"><span className="text-sm">{p.have_total.toLocaleString()} of {p.target_total.toLocaleString()} candles</span><span className="font-semibold tabular-nums">{(p.progress * 100).toFixed(0)}%</span></div>
              <div className="h-2.5 rounded bg-line"><div className="h-full rounded bg-acc" style={{ width: `${Math.max(1, p.progress * 100)}%` }} /></div>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Pairs at target" value={`${p.pairs_done} / ${p.pairs_total}`} sub={`${perPair.toLocaleString()} candles each`} />
              <Stat label="Streaming now" value={p.streaming_pairs} sub={`+${p.live_rate_per_hour} candles / hour`} tone={p.streaming_pairs ? 'up' : 'warn'} />
              <Stat label="Live capture alone" value={p.eta_hours_live_only === null ? 'never' : days(p.eta_hours_live_only)} sub="to reach the target" tone="warn" />
              <Stat label="Scrolling charts back" value="minutes" sub="each pair loads days at once" tone="up" />
            </div>
            <div className="rounded-lg border border-line bg-panel2 p-3 text-sm">
              <b>How to collect fast:</b> in your Quotex tab open a pair from the list below, then drag the chart to the right (or scroll back) until it reaches the date shown. The candles land in the database automatically - watch <i>History imports</i> to see them arrive. Work down the list: pairs you have already started come first, so each one ends up with a long unbroken history. Live pairs fill in by themselves while the tab stays open.
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Only pairs paying at least (%)"><input className="input !w-32" type="number" min={0} max={100} step={5} value={minPayout} onChange={(e) => setMinPayout(Number(e.target.value))} /></Field>
              <Field label="Total target (candles)"><input className="input !w-36" type="number" min={1000} step={10000} value={target} onChange={(e) => setTarget(Number(e.target.value))} /></Field>
              <Field label="Per pair (candles)" hint="1,440 = one day"><input className="input !w-32" type="number" min={100} step={500} value={perPair} onChange={(e) => setPerPair(Number(e.target.value))} /></Field>
            </div>
          </div>
        )}
      </Card>
      {p && (
        <Card title={`Pairs to collect (${p.pairs.filter((x) => !x.done).length} unfinished)`}>
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Pair', 'Payout', 'Stored', 'Toward per-pair target', 'Newest', 'What to do'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{p.pairs.map((x) => (
              <tr key={x.symbol} className={x.done || !x.open ? 'opacity-60' : ''}>
                <td className="td font-medium">{prettyPair(x.symbol)}<div className="note">{x.market}</div></td><td className="td tabular-nums">{x.payout}%</td>
                <td className="td tabular-nums">{x.candles.toLocaleString()}</td>
                <td className="td w-44"><div className="flex items-center gap-2"><div className="h-1.5 flex-1 rounded bg-line"><div className={`h-full rounded ${x.done ? 'bg-up' : 'bg-acc'}`} style={{ width: `${Math.min(100, (x.candles / perPair) * 100)}%` }} /></div><span className="note tabular-nums">{Math.min(100, Math.round((x.candles / perPair) * 100))}%</span></div></td>
                <td className="td note">{x.last ? ago(x.last, now) : '–'}</td><td className="td text-sm">{todo(x)}</td></tr>))}</tbody></table></div>
        </Card>
      )}
    </div>
  );
}

export default function DataPage() {
  const live = useLiveState();
  const cov = useCoverage();
  const isAdmin = useMe().data?.user.role === 'admin';            // the shared-candle controls are the admin's
  const [tab, setTab] = useState('plan');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<{ symbol: string; tf: number } | null>(null);
  const gaps = useGaps(open?.symbol ?? null, open?.tf ?? 60);

  if (!live.data) return <Spinner />;
  const now = live.data.now;
  const syms = live.data.symbols.filter((s) => (s.name + s.symbol).toLowerCase().includes(q.toLowerCase()));
  const rows = (cov.data ?? []).filter((c) => c.symbol.toLowerCase().includes(q.toLowerCase()));
  const totalCandles = (cov.data ?? []).reduce((a, c) => a + c.candles, 0);

  return (
    <>
      <PageTitle title="Data" sub={`${totalCandles.toLocaleString()} candles stored across ${new Set((cov.data ?? []).map((c) => c.symbol)).size} pairs. Backtests and the clinic can only be as good as this history - scroll a Quotex chart back to load older candles.`}
        right={<div className="flex items-center gap-2"><input className="input !w-56" placeholder="Filter pairs…" value={q} onChange={(e) => setQ(e.target.value)} /><a className="btn btn-sm" href="/api/data/export?fmt=csv" download title="Every stored candle of every pair, as one CSV">Export all CSV</a></div>} />
      <Tabs tabs={[{ key: 'plan', label: 'Collection plan' }, { key: 'sentiment', label: 'Sentiment' }, { key: 'coverage', label: 'Candle coverage' }, { key: 'instruments', label: `Instruments (${live.data.symbols.length})` }, { key: 'history', label: 'History imports' },
        ...(isAdmin ? [{ key: 'shared', label: 'Shared candles' }, { key: 'feed', label: 'Live feed' }] : [])]} value={tab} onChange={setTab} />

      {tab === 'plan' && <PlanTab now={now} />}
      {tab === 'shared' && isAdmin && <ContribPanel now={now} />}
      {tab === 'feed' && isAdmin && <FeedPanel now={now} />}
      {tab === 'sentiment' && <SentimentPanel now={now} />}

      {tab === 'coverage' && (
        <Card>
          {!cov.data ? <Spinner /> : rows.length === 0 ? <Empty>No candles stored yet.</Empty> : (
            <div className="overflow-x-auto"><table className="w-full">
              <thead><tr>{['Pair', 'TF', 'Candles', 'From', 'To', 'Span', 'Missing', 'Completeness', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{rows.map((c) => (
                <Fragment key={`${c.symbol}-${c.tf}`}><tr><td className="td font-medium">{prettyPair(c.symbol)}</td><td className="td">{tfName(c.tf)}</td><td className="td tabular-nums">{c.candles.toLocaleString()}</td>
                  <td className="td note">{dateTime(c.first)}</td><td className="td note">{dateTime(c.last)} <span className="text-mut">({ago(c.last, now)})</span></td>
                  <td className="td tabular-nums note">{((c.last - c.first) / 3600).toFixed(1)} h</td><td className="td tabular-nums">{c.missing}</td>
                  <td className="td w-40"><div className="flex items-center gap-2"><div className="h-1.5 flex-1 rounded bg-line"><div className={`h-full rounded ${c.completeness > 0.97 ? 'bg-up' : c.completeness > 0.9 ? 'bg-warn' : 'bg-down'}`} style={{ width: pct(c.completeness, 0) }} /></div><span className="note tabular-nums">{pct(c.completeness, 1)}</span></div></td>
                  <td className="td whitespace-nowrap"><button className="btn btn-sm" onClick={() => setOpen(open?.symbol === c.symbol && open.tf === c.tf ? null : { symbol: c.symbol, tf: c.tf })}>{open?.symbol === c.symbol && open.tf === c.tf ? 'Hide gaps' : 'Gaps'}</button>{' '}
                    <a className="btn btn-sm" href={`/api/data/export?symbol=${encodeURIComponent(c.symbol)}&tf=${c.tf}&fmt=csv`} download>CSV</a></td></tr>
                {open?.symbol === c.symbol && open.tf === c.tf && (
                  <tr><td className="td bg-panel2" colSpan={9}>{!gaps.data ? <Spinner /> : gaps.data.gaps === 0 ? <span className="note">No holes: every {tfName(c.tf)} candle between the first and last is stored.</span> : (
                    <div><p className="note mb-2">{gaps.data.gaps} holes, {gaps.data.missing} candles missing (newest first). Live (non-OTC) pairs also show a hole at every market close - those are not errors. To fill a hole, open this pair on Quotex and scroll the chart back to that time.</p>
                      <div className="flex flex-wrap gap-2">{gaps.data.holes.slice(0, 40).map((h) => <Badge key={h.after} tone="warn" title={`${dateTime(h.after)} -> ${dateTime(h.before)}`}>{dateTime(h.after + c.tf)} · {h.missing} missing</Badge>)}</div></div>)}</td></tr>)}</Fragment>))}</tbody></table></div>
          )}
        </Card>
      )}

      {tab === 'instruments' && (
        <Card>
          <div className="overflow-x-auto"><table className="w-full">
            <thead><tr>{['Pair', 'Market', 'Category', 'Payout', 'Open on Quotex', 'Streaming', 'Last price'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{syms.map((s) => (
              <tr key={s.symbol}><td className="td font-medium">{s.name}<div className="note font-mono">{s.symbol}</div></td><td className="td"><Badge tone={s.market === 'OTC' ? 'acc' : 'mut'}>{s.market}</Badge></td>
                <td className="td note">{s.category}</td><td className="td tabular-nums">{s.payout ? `${s.payout}%` : '–'}</td>
                <td className="td">{s.open ? <Badge tone="up">open</Badge> : <Badge>closed</Badge>}</td><td className="td">{s.streaming ? <Badge tone="up">● live</Badge> : <span className="note">–</span>}</td>
                <td className="td tabular-nums">{s.last_price ?? '–'}</td></tr>))}</tbody></table></div>
          <p className="note mt-3">Only pairs open in your Quotex tab stream data. Open another pair on Quotex to start collecting it.</p>
        </Card>
      )}

      {tab === 'history' && (
        <Card title="Recent history messages from the Quotex page" right={<span className="note">candles the page loaded and the backend accepted</span>}>
          {!live.data.ingest.hist?.length ? <Empty>No history received since the backend started. Hard-reload the Quotex tab or scroll the chart back.</Empty> : (
            <table className="w-full"><thead><tr>{['When', 'Pair', 'Rows', 'New to DB', 'Covers', 'Timeframe', 'Result'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{[...live.data.ingest.hist].reverse().map((h, i) => (
                <tr key={i}><td className="td note">{ago(h.at, now)}</td><td className="td">{prettyPair(h.asset)}</td><td className="td tabular-nums">{h.rows}</td><td className="td tabular-nums">{h.new ?? '–'}</td><td className="td note">{h.from && h.to ? `${dateTime(h.from)} → ${dateTime(h.to)}` : '–'}</td><td className="td">{h.tf ? tfName(h.tf) : '?'}</td>
                  <td className="td">{h.result === 'seeded' || h.result.startsWith('stored') ? <Badge tone="up">{h.result}</Badge> : <Badge tone="warn">{h.result}</Badge>}</td></tr>))}</tbody></table>
          )}
        </Card>
      )}
    </>
  );
}
