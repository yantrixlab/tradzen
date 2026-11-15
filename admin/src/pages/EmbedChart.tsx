import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, qs } from '../api';
import type { LiveSelection } from '../hooks';
import { SmartChart } from '../components/chart/SmartChart';

/**
 * The full chart (supply / demand zones, HH/HL/LH/LL structure, strategy picker, zoom, overview strip) on its own, with no admin sidebar.
 * The extension's side panel shows it in a frame under its pair picker. The pair and timeframe come from the address (?symbol=&tf=) and the
 * parent page can change them later with postMessage({ qxs: 'pair', symbol, tf }), so the chart keeps its zoom and strategy while you switch pairs.
 */
export default function EmbedChart() {
  const p = new URLSearchParams(location.search);
  const [pick, setPick] = useState<{ symbol: string; tf: number }>({ symbol: p.get('symbol') ?? '', tf: Number(p.get('tf')) || 60 });
  const height = Number(p.get('h')) || 340;

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== location.origin) return;
      const d = e.data as { qxs?: string; symbol?: string; tf?: number } | null;
      if (d && d.qxs === 'pair' && d.symbol && d.tf) setPick((cur) => (cur.symbol === d.symbol && cur.tf === d.tf ? cur : { symbol: d.symbol!, tf: Number(d.tf) }));
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, []);

  const q = useQuery({
    queryKey: ['embed-sel', pick.symbol, pick.tf],
    queryFn: () => api<LiveSelection>(`/api/state${qs({ symbol: pick.symbol, tf: pick.tf })}`),
    refetchInterval: 1500, placeholderData: keepPreviousData,
  });
  const sel = q.data?.selected;

  if (!sel) return <div className="grid place-items-center p-6 text-center text-sm text-mut" style={{ height }}>{q.isError ? 'Backend unreachable.' : 'No pair streaming yet. Open a chart on Quotex with the extension enabled.'}</div>;
  return (
    <div className="p-2">
      <SmartChart symbol={sel.symbol} tf={sel.tf} live={{ candles: sel.candles, forming: sel.forming, lastPrice: sel.last_price }} height={height} initialCount={90} limit={1500} />
    </div>
  );
}
