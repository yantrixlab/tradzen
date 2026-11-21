import { describe, expect, it } from 'vitest';
import { addMonths, monthRange, monthsBack, monthsSpan, shade, weekTotal, weeksOf, weekdayNames, type DayStat } from './pnlCalendar';

const day = (net: number, n = 1): DayStat => ({ n, wins: net > 0 ? n : 0, losses: net < 0 ? n : 0, draws: 0, win_rate: null, staked: n, net, roi: null, best: null, worst: null, top_pair: null });

describe('weeksOf', () => {
  it('puts 1 Nov 2026 (a Sunday) in the right column for both week starts', () => {
    const sun = weeksOf({ y: 2026, m: 10 }, 0), mon = weeksOf({ y: 2026, m: 10 }, 1);
    expect(sun[0][0]).toBe('2026-11-01');
    expect(mon[0].slice(0, 6)).toEqual([null, null, null, null, null, null]);
    expect(mon[0][6]).toBe('2026-11-01');
    expect(sun.flat().filter(Boolean)).toHaveLength(30);
    expect(mon.flat().filter(Boolean)).toHaveLength(30);
    expect(sun.every((w) => w.length === 7) && mon.every((w) => w.length === 7)).toBe(true);
    const lastWeek = sun[sun.length - 1].filter(Boolean);
    expect(lastWeek[lastWeek.length - 1]).toBe('2026-11-30');
  });
  it('handles February of a leap year', () => {
    expect(weeksOf({ y: 2028, m: 1 }, 1).flat().filter(Boolean)).toHaveLength(29);
  });
});

describe('month helpers', () => {
  it('names the weekdays from the chosen start', () => {
    expect(weekdayNames(1)[0]).toBe('Mon');
    expect(weekdayNames(0)[0]).toBe('Sun');
    expect(weekdayNames(1)[6]).toBe('Sun');
  });
  it('walks months across a year boundary', () => {
    expect(addMonths({ y: 2026, m: 0 }, -1)).toEqual({ y: 2025, m: 11 });
    expect(monthsBack(new Date(2026, 10, 21), 3)).toEqual([{ y: 2026, m: 10 }, { y: 2026, m: 9 }, { y: 2026, m: 8 }]);
    expect(monthsSpan('2026-09-14', '2026-11-02')).toEqual([{ y: 2026, m: 10 }, { y: 2026, m: 9 }, { y: 2026, m: 8 }]);
    expect(monthsSpan('2025-11-01', '2026-11-01', 5)).toHaveLength(5);
    expect(monthsSpan('', '2026-11-01')).toEqual([]);
  });
  it('asks the server for the viewer\'s own midnights', () => {
    const r = monthRange({ y: 2026, m: 10 });
    expect(r.from).toBe(Math.floor(new Date(2026, 10, 1).getTime() / 1000));
    expect(r.to).toBe(Math.floor(new Date(2026, 11, 1).getTime() / 1000));
  });
});

describe('totals and colours', () => {
  it('sums a week from the days that have trades', () => {
    const days = { '2026-11-02': day(5, 2), '2026-11-03': day(-1.5, 1) };
    expect(weekTotal(['2026-11-02', '2026-11-03', '2026-11-04', null, null, null, null], days)).toEqual({ net: 3.5, n: 3, days: 2 });
    expect(weekTotal([null, null, null, null, null, null, null], days)).toEqual({ net: 0, n: 0, days: 0 });
  });
  it('shades green for profit, red for loss, grey for flat and stronger for bigger days', () => {
    expect(shade(5, 5)).toMatch(/^rgba\(34,197,94,/);
    expect(shade(-5, 5)).toMatch(/^rgba\(239,68,68,/);
    expect(shade(0, 5)).toMatch(/^rgba\(148,163,184,/);
    const alpha = (s: string) => parseFloat(s.split(',')[3]);
    expect(alpha(shade(5, 5))).toBeGreaterThan(alpha(shade(0.5, 5)));
    expect(alpha(shade(0.01, 5))).toBeGreaterThanOrEqual(0.2);
    expect(alpha(shade(50, 5))).toBeLessThanOrEqual(0.8);
  });
});
