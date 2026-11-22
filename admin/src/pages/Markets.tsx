import { useEffect, useMemo, useRef, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useCheckout } from '../components/CheckoutHost';
import { api, ApiError } from '../api';
import { prettyPair, tfName } from '../format';
import { usePersisted } from '../hooks';
import { Badge, Card, Empty, ErrorBox, Spinner } from '../components/ui';
import { SmartChart } from '../components/chart/SmartChart';
import { STATUS_TEXT, countdown, groupMarkets, secondsLeft, type MarketList, type MarketLive, type MarketRow } from '../lib/markets';

const TFS = [60, 300, 900];

/** The market list: OTC and live markets by category, a padlock on the ones the plan does not open, a dot on the ones with live prices. */
function Sidebar({ list, sym, onPick }: { list: MarketList; sym: string; onPick: (m: MarketRow) => void }) {
  const [q, setQ] = useState('');
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const groups = useMemo(() => groupMarkets(list.pairs, q), [list.pairs, q]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <input className="input mb-3" placeholder="Search markets…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search markets" />
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {groups.length === 0 && <Empty>No market matches.</Empty>}
        {groups.map((g) => {
          const shut = closed[g.key] && !q;
          return (
            <div key={g.key}>
              <button type="button" className="mb-1 flex w-full items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-mut hover:text-fg" aria-expanded={!shut} onClick={() => setClosed({ ...closed, [g.key]: !closed[g.key] })}>
                <span>{g.label}</span><span>{shut ? '▸' : '▾'} {g.pairs.length}</span>
              </button>
              {!shut && <ul className="space-y-0.5">{g.pairs.map((p) => (
                <li key={p.symbol}>
                  <button type="button" onClick={() => onPick(p)} aria-current={sym === p.symbol} title={p.locked ? 'Part of a paid plan' : p.open ? '' : 'This market is closed now'}
                    className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition ${sym === p.symbol ? 'bg-acc/20 text-fg' : 'text-fg/90 hover:bg-panel2'} ${p.open ? '' : 'opacity-60'}`}>
                    <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${p.streaming ? 'bg-up' : 'bg-line'}`} aria-label={p.streaming ? 'live prices' : 'no live prices'} />
                    <span className="min-w-0 flex-1 truncate">{prettyPair(p.symbol)}</span>
                    {p.payout ? <span className="shrink-0 text-[11px] tabular-nums text-mut">{p.payout}%</span> : null}
                    {p.locked && <span className="shrink-0 rounded-md bg-warn/15 px-1.5 py-0.5 text-[11px] text-warn" aria-label="locked">🔒</span>}
                  </button>
                </li>))}</ul>}
            </div>);
        })}
      </div>
      <p className="note mt-3">{list.locking && !list.all_open ? `Your plan opens ${list.free_pairs.length} market${list.free_pairs.length === 1 ? '' : 's'}. ` : ''}A green dot means the server is receiving live prices for that pair.</p>
    </div>
  );
}

function Paywall({ pair, plan }: { pair: string; plan: string | null }) {
  const { open } = useCheckout();
  return (
    <Card>
      <div className="mx-auto max-w-md py-8 text-center">
        <div className="text-4xl">🔒</div>
        <h2 className="mt-2 text-xl font-bold">{prettyPair(pair)} is part of a paid plan</h2>
        <p className="mt-2 text-sm text-mut">{plan ? `Your plan (${plan}) opens only some markets. ` : ''}Choose a plan to unlock every OTC and live market with live candles and the countdown to each close.</p>
        <button type="button" className="btn btn-primary mt-4" onClick={() => open()}>See the plans</button>
      </div>
    </Card>
  );
}

/** The chart platform: pick a market on the left, watch its live candles and the countdown to the close. Prices come from the server; what a plan may open is decided on the server too. */
export default function Markets() {
  const [sym, setSym] = usePersisted('markets-pair', '');
  const [tf, setTf] = usePersisted('markets-tf', 60);
  const [paywall, setPaywall] = useState<string | null>(null);
  const list = useQuery({ queryKey: ['markets'], queryFn: () => api<MarketList>('/api/markets'), refetchInterval: 10000, placeholderData: keepPreviousData });
  const rows = list.data?.pairs ?? [];
  const current = rows.find((p) => p.symbol === sym && !p.locked);
  const fallback = useMemo(() => rows.find((p) => p.streaming && !p.locked) ?? rows.find((p) => !p.locked && p.open) ?? rows.find((p) => !p.locked), [rows]);
  const symbol = paywall ? '' : current?.symbol ?? fallback?.symbol ?? '';
  const live = useQuery({
    queryKey: ['market-live', symbol, tf], queryFn: () => api<MarketLive>(`/api/markets/${encodeURIComponent(symbol)}/live?tf=${tf}`),
    enabled: !!symbol, refetchInterval: 1000, placeholderData: keepPreviousData, retry: false,
  });
  const d = live.data && live.data.symbol === symbol ? live.data : undefined;
  const skew = useRef(0);
  const [, tick] = useState(0);
  useEffect(() => { if (live.dataUpdatedAt && live.data) skew.current = live.data.now - live.dataUpdatedAt / 1000; }, [live.dataUpdatedAt, live.data]);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 250); return () => clearInterval(t); }, []);
  const locked = live.error instanceof ApiError && live.error.status === 403;

  if (list.isError) return <ErrorBox error={list.error} />;
  if (!list.data) return <Spinner />;
  const pick = (m: MarketRow) => { if (m.locked) { setPaywall(m.symbol); return; } setPaywall(null); setSym(m.symbol); };
  const left = d ? secondsLeft(d.closes_at, skew.current) : null;
  const price = d?.last_price ?? d?.forming?.c ?? d?.candles[d.candles.length - 1]?.c ?? null;
  const status = d?.status ?? 'waiting';

  return (
    <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="card order-2 max-h-[70vh] min-h-0 xl:order-1 xl:sticky xl:top-2 xl:max-h-[calc(100vh-6rem)]">
        <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-bold">Markets</h2><span className="note">{list.data.plan ?? ''}</span></div>
        <Sidebar list={list.data} sym={paywall ?? symbol} onPick={pick} />
      </aside>

      <div className="order-1 min-w-0 xl:order-2">
        {paywall || locked ? <Paywall pair={paywall ?? symbol} plan={list.data.plan} /> : !symbol ? <Card><Empty>No market is open to your plan right now.</Empty></Card> : (
          <Card>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-extrabold sm:text-2xl">{prettyPair(symbol)}</h1>
                  {d && <Badge tone={d.market === 'OTC' ? 'acc' : d.market === 'CRYPTO' ? 'warn' : 'mut'}>{d.market}</Badge>}{d?.payout ? <Badge tone="up">{d.payout}%</Badge> : null}
                  <Badge tone={status === 'live' ? 'up' : status === 'closed' ? 'mut' : 'warn'} title={status === 'waiting' ? 'No live prices are arriving for this pair right now: the candles shown are the stored ones' : undefined}>
                    {status === 'live' ? '● ' : ''}{STATUS_TEXT[status]}</Badge></div>
                <div className="mt-1 flex items-baseline gap-3"><span className="text-2xl font-bold tabular-nums sm:text-3xl">{price ?? '–'}</span></div>
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                {left !== null && status !== 'closed' && <div className="text-right"><div className="note">Candle closes in</div><div className="text-2xl font-bold tabular-nums" aria-live="off">{countdown(left)}</div></div>}
                <div className="inline-flex overflow-hidden rounded-lg border border-line text-sm font-semibold" role="group" aria-label="Timeframe">
                  {TFS.map((t) => <button key={t} onClick={() => setTf(t)} aria-pressed={tf === t} className={`border-r border-line px-3 py-1.5 last:border-r-0 ${tf === t ? 'bg-acc text-white' : 'bg-panel2 text-mut hover:text-fg'}`}>{tfName(t)}</button>)}
                </div>
              </div>
            </div>
            {live.isError && !locked && <div className="mb-3"><ErrorBox error={live.error} /></div>}
            <SmartChart key={`${symbol}-${tf}`} symbol={symbol} tf={tf} live={d ? { candles: d.candles, forming: d.forming, lastPrice: d.last_price } : undefined} height={420} initialCount={120} limit={1500} />
            {d?.market === 'CRYPTO' && <p className="note mt-2">Crypto prices come from Binance's public market data, not from Quotex, and this market is for charting only: Tradzen sends no signals and keeps no record of trades for it.</p>}
            {status === 'waiting' && <p className="note mt-2">The server is not receiving live prices for this pair at the moment, so the chart shows the candles stored so far and will move again when prices resume.</p>}
          </Card>)}
      </div>
    </div>
  );
}
