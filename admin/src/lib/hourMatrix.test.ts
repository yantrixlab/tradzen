import { describe, expect, it } from 'vitest';
import { addStat, buildMatrix, cellBackground, emptyStat, hourLabel, hourRange, insightsOf, metricOf, scaleOf, type HourCell } from './hourMatrix';

const cell = (d: number, h: number, wins: number, losses: number, net: number, draws = 0): HourCell => ({
  d, h, n: wins + losses + draws, wins, losses, draws, win_rate: wins + losses ? wins / (wins + losses) : null, staked: wins + losses + draws, net, roi: null,
});

describe('buildMatrix', () => {
  const cells = [cell(1, 10, 2, 1, 0.6), cell(1, 10 + 0, 0, 0, 0), cell(0, 10, 1, 3, -2), cell(3, 15, 5, 0, 4), cell(6, 23, 0, 2, -2)];

  it('puts a slot in the column of its weekday, whichever day the week starts on', () => {
    const mon = buildMatrix(cells, 1);                              // Mon, Tue, Wed, Thu, Fri, Sat, Sun
    expect(mon.rows[10][0]?.n).toBe(3);                             // Monday 10:00
    expect(mon.rows[10][6]?.n).toBe(4);                             // Sunday 10:00 is the last column
    expect(mon.rows[15][2]?.n).toBe(5);                             // Wednesday 15:00
    expect(mon.rows[23][5]?.n).toBe(2);                             // Saturday 23:00
    const sun = buildMatrix(cells, 0);                              // Sun, Mon, Tue ...
    expect(sun.rows[10][0]?.n).toBe(4);
    expect(sun.rows[10][1]?.n).toBe(3);
  });

  it('totals per hour, per day and overall, and ignores empty or out-of-range slots', () => {
    const m = buildMatrix([...cells, cell(2, 30, 1, 0, 1), cell(9, 5, 1, 0, 1)], 1);
    expect(m.total.n).toBe(14);
    expect(m.total.net).toBe(0.6);
    expect(m.hourTotals[10].n).toBe(7);
    expect(m.hourTotals[10].net).toBe(-1.4);
    expect(m.hourTotals[10].win_rate).toBeCloseTo(3 / 7);
    expect(m.dayTotals[0].n).toBe(3);                               // Monday column
    expect(m.hourTotals[4]).toEqual(emptyStat());
  });
});

describe('addStat', () => {
  it('keeps the win rate over wins and losses only (a draw is neither)', () => {
    const s = addStat(cell(0, 0, 1, 1, 0, 2), cell(0, 1, 2, 0, 1.5));
    expect([s.n, s.wins, s.losses, s.draws]).toEqual([6, 3, 1, 2]);
    expect(s.win_rate).toBe(0.75);
    expect(s.net).toBe(1.5);
  });
});

describe('metricOf', () => {
  it('returns the number for each view, null for an empty slot', () => {
    const s = cell(1, 1, 3, 1, 2.5);
    expect(metricOf(s, 'trades')).toBe(4);
    expect(metricOf(s, 'net')).toBe(2.5);
    expect(metricOf(s, 'rate')).toBe(75);
    expect(metricOf(null, 'trades')).toBeNull();
    expect(metricOf(cell(1, 1, 0, 0, 0, 2), 'rate')).toBeNull();     // only draws: no win rate
  });
});

describe('cellBackground', () => {
  const m = buildMatrix([cell(1, 9, 4, 0, 3.2), cell(1, 10, 0, 4, -4)], 1);
  it('is green for a profit and red for a loss, deeper the bigger it is', () => {
    const scale = scaleOf(m, 'net');
    expect(scale).toBe(4);
    expect(cellBackground(m.rows[9][0], 'net', scale, null)).toContain('34,197,94');
    expect(cellBackground(m.rows[10][0], 'net', scale, null)).toContain('239,68,68');
    const strong = cellBackground(cell(1, 1, 1, 0, 4), 'net', 4, null)!, weak = cellBackground(cell(1, 1, 1, 0, 0.4), 'net', 4, null)!;
    const alpha = (css: string) => Number(/rgba\(34,197,94,([\d.]+)\)/.exec(css)![1]);
    expect(alpha(strong)).toBeGreaterThan(alpha(weak));
  });
  it('measures the win rate against break-even', () => {
    const win60 = cell(1, 1, 6, 4, 1);                               // 60 %
    expect(cellBackground(win60, 'rate', 0, 0.534)).toContain('34,197,94');
    expect(cellBackground(win60, 'rate', 0, 0.7)).toContain('239,68,68');
  });
  it('uses violet for the number of trades and nothing for an empty slot', () => {
    expect(cellBackground(cell(1, 1, 2, 2, 0), 'trades', 4, null)).toContain('139,92,246');
    expect(cellBackground(null, 'trades', 4, null)).toBeUndefined();
  });
});

describe('insightsOf', () => {
  it('finds the busiest hour and the best and worst hours and days, ignoring thin samples', () => {
    const m = buildMatrix([
      cell(1, 9, 5, 2, 3), cell(2, 9, 4, 1, 2),                      // 09:00: 12 trades, +5
      cell(1, 14, 1, 4, -3), cell(3, 14, 1, 2, -2),                  // 14:00: 8 trades, -5
      cell(1, 20, 1, 0, 50),                                         // 20:00: one lucky trade, not enough to be "best"
    ], 1);
    const i = insightsOf(m);
    expect(i.busiest?.index).toBe(9);
    expect(i.busiest?.share).toBeCloseTo(12 / 21);
    expect(i.best?.index).toBe(9);
    expect(i.worst?.index).toBe(14);
    expect(i.bestDay?.index).toBe(0);                                // Monday (column 0): +3 -3 +50 over 3+ trades
    expect(i.worstDay?.index).toBe(2);                               // Wednesday: 3 trades, -2
    expect(i.worstDay?.stat.net).toBe(-2);
  });
  it('has nothing to say about an empty grid', () => {
    expect(insightsOf(buildMatrix([], 1))).toEqual({ busiest: null, best: null, worst: null, bestDay: null, worstDay: null });
  });
});

describe('labels', () => {
  it('writes hours as 24 h clock times', () => {
    expect(hourLabel(7)).toBe('07:00');
    expect(hourRange(23)).toBe('23:00–00:00');
  });
});
