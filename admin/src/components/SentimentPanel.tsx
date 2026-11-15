import { ago, num, pct, prettyPair } from '../format';
import { useSentiment, useSentimentStudy, usePersisted } from '../hooks';
import { Badge, Card, Empty, Field, Spinner, Stat } from './ui';

/** Trader-sentiment collection: what we are receiving, and whether it predicts the next candle (the study fills in as data arrives). */
export function SentimentPanel({ now }: { now: number }) {
  const cov = useSentiment();
  const [minPayout, setMinPayout] = usePersisted('sent.minPayout', 80);
  const study = useSentimentStudy(minPayout);
  const c = cov.data;
  const s = study.data;
  const needTotal = (s?.needed_per_bucket ?? 1100) * 5;                       // five ranges, each needs enough candles
  const ready = s ? Math.min(1, s.matched / needTotal) : 0;
  const thin = (c?.pairs_receiving ?? 0) < 8;

  return (
    <div className="space-y-4">
      <Card title="Trader sentiment · the one input that is not derived from price">
        <div className="space-y-4">
          <p className="text-sm">Every strategy so far reads the same price series and lands at coin-flip accuracy. Quotex also shows what share of traders chose <b>up</b>. Whether the crowd is right, or reliably wrong, at the extremes is something price cannot tell us - so we record it for every pair and test it against the next candle (no look-ahead: only the reading known before the candle opened).</p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Receiving now" value={c ? `${c.pairs_receiving} / ${c.pairs_total}` : '–'} sub="pairs with a reading in the last 3 min" tone={c && c.pairs_receiving >= 8 ? 'up' : 'warn'} />
            <Stat label="Readings stored" value={c ? c.total_samples.toLocaleString() : '–'} sub={c ? `${c.pairs_with_data} pairs · over ${num(c.span_hours, 0)} h` : ''} />
            <Stat label="Matched to candles" value={s ? s.matched.toLocaleString() : '–'} sub={s ? `${s.pairs} pairs paying ≥ ${minPayout}%` : ''} />
            <Stat label="Ready for a fair test" value={`${(ready * 100).toFixed(0)}%`} sub={`${needTotal.toLocaleString()} matched candles needed`} tone={ready >= 1 ? 'up' : 'warn'} />
          </div>
          <div className="h-2.5 rounded bg-line"><div className="h-full rounded bg-acc" style={{ width: `${Math.max(1, ready * 100)}%` }} /></div>
          {thin && (
            <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">
              <b>Only {c?.pairs_receiving ?? 0} pairs are reporting right now.</b> Quotex sends sentiment only for the pairs its page is following. We saw bursts of up to 34 pairs at once, but most minutes carry 3 or fewer. Quotex's page decides this, and our extension is read-only, so it cannot ask for more. What you can do: keep the Quotex tab in the foreground on a pair, and try leaving the instrument/asset list open - then watch the <i>Receiving now</i> number above to see what works.
            </div>)}
        </div>
      </Card>

      <Card title="Does the crowd predict the next candle?" right={<div className="w-40"><Field label="Only pairs paying ≥ %"><input className="input" type="number" min={0} max={100} step={5} value={minPayout} onChange={(e) => setMinPayout(Number(e.target.value))} /></Field></div>}>
        {!s ? <Spinner /> : s.matched === 0 ? <Empty>{s.note ?? 'Nothing to test yet.'}</Empty> : (
          <div className="space-y-3">
            <div className={`rounded-lg border p-3 text-sm ${s.edge_found ? 'border-up/40 bg-up/10' : 'border-line bg-panel2'}`}>
              {s.edge_found ? <><b>A range stands out</b> (z ≥ 3, enough candles, same direction in both halves). Treat as a lead to paper-test, not a result - re-check as data grows.</>
                : <><b>No relationship proven.</b> That is the expected answer until there is more data; a range only counts once it has ≥ {s.needed_per_bucket.toLocaleString()} candles, |z| ≥ 3 and the same direction in both halves of the data.</>}
            </div>
            <table className="w-full">
              <thead><tr>{['Crowd says up', 'Candles', 'Candle closed up', 'z', 'Older half', 'Newer half', 'Status'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{s.buckets.map((b) => (
                <tr key={b.lo}>
                  <td className="td font-medium">{b.lo.toFixed(0)}–{b.hi.toFixed(0)}%</td><td className="td tabular-nums">{b.n.toLocaleString()}</td>
                  <td className="td tabular-nums">{pct(b.up_rate)}</td><td className="td tabular-nums">{b.z === null ? '–' : (b.z >= 0 ? '+' : '') + b.z.toFixed(1)}</td>
                  <td className="td tabular-nums note">{pct(b.first_half)} <span className="text-mut">({b.n_first})</span></td><td className="td tabular-nums note">{pct(b.second_half)} <span className="text-mut">({b.n_second})</span></td>
                  <td className="td">{!b.enough ? <Badge tone="warn" title={`needs ${s.needed_per_bucket} candles`}>TOO FEW</Badge> : b.stable ? <Badge tone="acc">SAME SIDE BOTH HALVES</Badge> : <Badge>MIXED</Badge>}</td>
                </tr>))}</tbody></table>
            <div className="note whitespace-normal normal-case tracking-normal">50% = a coin flip. z counts standard errors away from 50%. With five ranges tested, a z around 2 appears by luck now and then - that is why 3 is required, together with both halves agreeing.</div>
          </div>)}
      </Card>

      <Card title="Coverage by pair">
        {!c ? <Spinner /> : (
          <div className="max-h-[420px] overflow-auto"><table className="w-full">
            <thead><tr>{['Pair', 'Payout', 'Readings', `Last ${Math.round(c.window_s / 60)} min`, 'Newest', 'Status'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{c.pairs.filter((p) => p.samples > 0 || p.receiving).map((p) => (
              <tr key={p.symbol}><td className="td font-medium">{prettyPair(p.symbol)}</td><td className="td tabular-nums">{p.payout ? `${p.payout}%` : '–'}</td>
                <td className="td tabular-nums">{p.samples.toLocaleString()}</td><td className="td tabular-nums">{p.recent}</td>
                <td className="td note">{p.last ? ago(p.last, now) : '–'}</td><td className="td">{p.receiving ? <Badge tone="up">● receiving</Badge> : <span className="note">quiet</span>}</td></tr>))}</tbody></table></div>)}
      </Card>
    </div>
  );
}
