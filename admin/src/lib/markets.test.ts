import { describe, expect, it } from 'vitest';
import { countdown, groupMarkets, secondsLeft, type MarketRow } from './markets';

const row = (symbol: string, over: Partial<MarketRow> = {}): MarketRow => ({ symbol, name: symbol.replace('_otc', ' (OTC)'), market: symbol.endsWith('_otc') ? 'OTC' : 'LIVE', category: 'currency', payout: 80, open: true, streaming: false, last_price: null, locked: false, ...over });

describe('groupMarkets', () => {
  const pairs = [row('EURUSD_otc'), row('BTCUSD_otc', { category: 'cryptocurrency' }), row('AUDCAD_otc', { locked: true }), row('USDJPY', { open: false }), row('XAUUSD', { category: 'commodity' })];
  it('puts OTC before live and splits each by category', () => {
    expect(groupMarkets(pairs).map((g) => g.label)).toEqual(['OTC markets · Currencies', 'OTC markets · Crypto', 'Live markets · Currencies', 'Live markets · Commodities']);
  });
  it('lists the pairs a person can open before the locked ones, open markets before closed ones', () => {
    expect(groupMarkets(pairs)[0].pairs.map((p) => p.symbol)).toEqual(['EURUSD_otc', 'AUDCAD_otc']);
    expect(groupMarkets([row('B', { open: false }), row('A')])[0].pairs.map((p) => p.symbol)).toEqual(['A', 'B']);
  });
  it('searches by symbol or name, ignoring case and separators', () => {
    expect(groupMarkets(pairs, 'eur usd').flatMap((g) => g.pairs.map((p) => p.symbol))).toEqual(['EURUSD_otc']);
    expect(groupMarkets(pairs, 'btc').flatMap((g) => g.pairs.map((p) => p.symbol))).toEqual(['BTCUSD_otc']);
    expect(groupMarkets(pairs, 'zzz')).toEqual([]);
  });
});

describe('countdown', () => {
  it('formats the seconds left and never goes below zero', () => {
    expect(countdown(48)).toBe('0:48');
    expect(countdown(725)).toBe('12:05');
    expect(countdown(0.2)).toBe('0:01');
    expect(countdown(-5)).toBe('0:00');
  });
  it('follows the server clock, not the computer clock', () => {
    // the server says the candle closes at 1000; the computer is 30 s fast (skew = server - browser = -30)
    expect(secondsLeft(1000, -30, 960)).toBe(70);
    expect(secondsLeft(1000, 0, 1005)).toBe(0);
  });
});
