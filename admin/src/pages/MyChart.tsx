import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api';
import type { LiveSelection } from '../hooks';
import { Card, Empty, ErrorBox, Spinner } from '../components/ui';
import { LiveView, PairPicker } from './LiveSignal';

type MyChartData = Omit<LiveSelection, 'alerts_enabled'> & { following: boolean; mine: { symbol: string; tf: number; age: number } | null };

/**
 * Everybody's own live chart: the pair and timeframe open on THIS account's Quotex tab. It follows them when you switch pairs there, until you pick a pair or a
 * timeframe here yourself ("Follow my Quotex tab again" hands control back). The same page is what the extension's side panel shows to ordinary accounts.
 */
export default function MyChart() {
  const compact = new URLSearchParams(location.search).has('embed');
  const [pick, setPick] = useState('');          // a pair chosen by hand; empty = follow the Quotex tab
  const [tfPick, setTfPick] = useState(0);       // a timeframe chosen by hand; 0 = follow the Quotex tab
  const q = useQuery({
    queryKey: ['mychart', pick, tfPick],
    queryFn: () => api<MyChartData>(`/api/mychart${qs({ symbol: pick, tf: tfPick || undefined })}`),
    refetchInterval: 1500, placeholderData: keepPreviousData,
  });
  const d = q.data;
  const sel = d?.selected ?? null;
  const following = !pick && !tfPick;
  const choose = (s: string) => { setPick(s); if (!s) setTfPick(0); };
  const picker = d ? <PairPicker data={d as unknown as LiveSelection} sym={sel?.symbol ?? ''} onPick={choose} following={following} /> : <Card><Spinner /></Card>;
  const view = q.isError ? <ErrorBox error={q.error} /> : !d ? <Card><Spinner /></Card> : sel
    ? <LiveView sel={sel} tf={sel.tf} setTf={setTfPick} />
    : <Card><Empty>No pair yet. Open a chart on Quotex (with the extension signed in) and this page follows the pair you open there, or pick a pair below.</Empty></Card>;
  return (
    <>
      {!compact && <div className="mb-4"><h1 className="text-xl font-bold sm:text-2xl">Live chart</h1>
        <p className="mt-1 text-sm text-mut">The pair open on your Quotex tab. Switch pairs there and the chart follows; pick one here to hold it.</p></div>}
      {compact ? (
        <div className="space-y-3">{view}<details className="card p-3"><summary className="cursor-pointer text-sm font-semibold">Choose another pair</summary><div className="mt-2">{picker}</div></details></div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]"><div className="order-2 xl:order-1">{picker}</div><div className="order-1 min-w-0 xl:order-2">{view}</div></div>
      )}
    </>
  );
}
