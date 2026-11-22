export const pct = (x: number | null | undefined, d = 1): string => (x === null || x === undefined || Number.isNaN(x) ? '–' : `${(x * 100).toFixed(d)}%`);
export const num = (x: number | null | undefined, d = 2): string => (x === null || x === undefined || Number.isNaN(x) ? '–' : x.toFixed(d));
export const signed = (x: number | null | undefined, d = 3): string => (x === null || x === undefined ? '–' : `${x >= 0 ? '+' : ''}${x.toFixed(d)}`);
export const TF_NAME: Record<number, string> = { 60: '1m', 300: '5m', 900: '15m' };
export const tfName = (tf: number): string => TF_NAME[tf] ?? `${tf}s`;
export const time = (t: number | null | undefined): string =>
  t ? new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–';
export const dateTime = (t: number | null | undefined): string =>
  t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–';
export const ago = (t: number | null | undefined, now = Date.now() / 1000): string => {
  if (!t) return '–';
  const s = Math.max(0, now - t);
  return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : s < 129600 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};
export const bytes = (b: number): string => (b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b > 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b} B`);
export const prettyPair = (sym: string): string => {
  if (sym.endsWith('_bn')) {                                   // a crypto market from Binance: BTCUSDT_bn -> BTC/USDT
    const b = sym.slice(0, -3);
    return b.endsWith('USDT') && b.length > 4 ? `${b.slice(0, -4)}/USDT` : b;
  }
  const base = sym.replace('_otc', '');
  return base.length === 6 ? `${base.slice(0, 3)}/${base.slice(3)}${sym.endsWith('_otc') ? ' (OTC)' : ''}` : sym;
};
/** verdict of an accuracy interval against the break-even rate */
export const verdict = (settled: number, lo: number | null, hi: number | null, be: number | null): 'above' | 'below' | 'near' | 'sample' | 'none' => {
  if (!settled) return 'none';
  if (settled < 30 || lo === null || hi === null || be === null) return 'sample';
  return lo > be ? 'above' : hi < be ? 'below' : 'near';
};
