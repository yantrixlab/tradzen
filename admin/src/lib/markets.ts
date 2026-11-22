/** Types and pure helpers of the Markets page (the chart platform): the list with padlocks, the live answer of one pair, grouping and the countdown. The server decides what is locked. */
import type { Candle } from '../api';

export interface MarketRow { symbol: string; name: string; market: string | null; category: string; payout: number | null; open: boolean; streaming: boolean; last_price: number | null; locked: boolean }
export interface MarketList { pairs: MarketRow[]; all_open: boolean; locking: boolean; free_pairs: string[]; plan: string | null; now: number }
export interface MarketLive {
  now: number; symbol: string; name: string; market: string; payout: number | null; tf: number; open: boolean; streaming: boolean; age: number | null; status: 'live' | 'waiting' | 'closed';
  last_price: number | null; forming: Candle | null; candles: Candle[]; closes_at: number; gap: unknown;
}
export interface MarketGroup { key: string; label: string; pairs: MarketRow[] }

const CAT_LABEL: Record<string, string> = { currency: 'Currencies', cryptocurrency: 'Crypto', commodity: 'Commodities', stock: 'Indices & stocks' };
const CAT_ORDER = ['currency', 'cryptocurrency', 'commodity', 'stock'];
const norm = (s: string) => s.toLowerCase().replace(/[\s/_()-]/g, '');

/** The sidebar's sections: OTC markets first, then live markets, each split by category, then the crypto markets (Binance prices, one section); a search keeps only the pairs that match;
 *  open pairs and pairs the person can open come first. */
const CRYPTO_ORDER = ['BTC', 'ETH', 'BNB', 'XRP', 'SOL', 'ADA', 'DOGE', 'AVAX', 'LINK', 'LTC', 'TRX', 'DOT'];          // the crypto list starts with the biggest coins
const cryptoRank = (p: MarketRow) => { const i = CRYPTO_ORDER.findIndex((c) => p.symbol.startsWith(`${c}USDT`)); return i < 0 ? CRYPTO_ORDER.length : i; };
const marketOf = (p: MarketRow): 'OTC' | 'LIVE' | 'CRYPTO' => (p.market === 'OTC' ? 'OTC' : p.market === 'CRYPTO' ? 'CRYPTO' : 'LIVE');
export function groupMarkets(pairs: MarketRow[], query = ''): MarketGroup[] {
  const q = norm(query.trim());
  const keep = pairs.filter((p) => !q || norm(`${p.symbol}${p.name}`).includes(q));
  const out: MarketGroup[] = [];
  for (const [mk, ml] of [['OTC', 'OTC markets'], ['LIVE', 'Live markets'], ['CRYPTO', 'Crypto · Binance prices']] as const) {
    const inMarket = keep.filter((p) => marketOf(p) === mk);
    const cats = [...new Set(inMarket.map((p) => p.category || 'other'))].sort((a, b) => (CAT_ORDER.indexOf(a) + 99) % 99 - (CAT_ORDER.indexOf(b) + 99) % 99 || a.localeCompare(b));
    for (const c of cats) {
      const list = inMarket.filter((p) => (p.category || 'other') === c).sort((a, b) => Number(b.open) - Number(a.open) || Number(a.locked) - Number(b.locked) || (mk === 'CRYPTO' ? cryptoRank(a) - cryptoRank(b) : 0) || a.name.localeCompare(b.name));
      if (list.length) out.push({ key: `${mk}-${c}`, label: mk === 'CRYPTO' ? ml : `${ml} · ${CAT_LABEL[c] ?? 'Other'}`, pairs: list });
    }
  }
  return out;
}

/** "0:48" / "12:05" for the seconds left to a candle's close; never negative. */
export const countdown = (secs: number): string => {
  const s = Math.max(0, Math.ceil(secs));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/** Seconds to a close on the SERVER's clock: `skew` = serverNow - the browser's clock at the moment the answer arrived (so a wrong computer clock does not matter). */
export const secondsLeft = (closesAt: number, skew: number, browserNow = Date.now() / 1000): number => Math.max(0, closesAt - (browserNow + skew));

export const STATUS_TEXT: Record<MarketLive['status'], string> = { live: 'LIVE', waiting: 'WAITING FOR PRICES', closed: 'MARKET CLOSED' };
