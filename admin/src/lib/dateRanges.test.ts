import { describe, expect, it } from 'vitest';
import { rangeOf, ymd } from './dateRanges';

const at = (y: number, m: number, d: number, h = 15) => new Date(y, m - 1, d, h, 30);
const day = (t: number | null) => (t === null ? null : ymd(new Date(t * 1000)));

describe('rangeOf', () => {
  const now = at(2026, 11, 18);                                      // a Wednesday
  it('today starts at this midnight and has no end', () => {
    expect(day(rangeOf('today', now).from)).toBe('2026-11-18');
    expect(rangeOf('today', now).to).toBeNull();
  });
  it('yesterday is exactly the previous calendar day', () => {
    const r = rangeOf('yesterday', now);
    expect(day(r.from)).toBe('2026-11-17');
    expect(day(r.to)).toBe('2026-11-18');
    expect(r.to! - r.from!).toBeGreaterThanOrEqual(23 * 3600);
  });
  it('weeks start on Monday', () => {
    expect(day(rangeOf('week', now).from)).toBe('2026-11-16');
    const last = rangeOf('lastweek', now);
    expect([day(last.from), day(last.to)]).toEqual(['2026-11-09', '2026-11-16']);
  });
  it('last month is the whole previous calendar month, also across a year end', () => {
    const r = rangeOf('lastmonth', now);
    expect([day(r.from), day(r.to)]).toEqual(['2026-10-01', '2026-11-01']);
    const jan = rangeOf('lastmonth', at(2027, 1, 5));
    expect([day(jan.from), day(jan.to)]).toEqual(['2026-12-01', '2027-01-01']);
  });
  it('rolling ranges count today as the first day', () => {
    expect(day(rangeOf('7d', now).from)).toBe('2026-11-12');
    expect(day(rangeOf('30d', now).from)).toBe('2026-10-20');
  });
  it('a custom range includes its last day, and all time has no limits', () => {
    const r = rangeOf('custom', now, { from: '2026-11-01', to: '2026-11-03' });
    expect([day(r.from), day(r.to)]).toEqual(['2026-11-01', '2026-11-04']);
    expect(rangeOf('all', now)).toEqual({ from: null, to: null });
  });
});
