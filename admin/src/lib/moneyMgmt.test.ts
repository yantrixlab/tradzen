import { describe, expect, it } from 'vitest';
import { clampSettings, DEFAULT_SETTINGS, dots, PRESETS, presetOf, meterLevel, sameSettings, share, stakePct, streakText, theme, type MmRecent } from './moneyMgmt';

describe('theme', () => {
  it('is green for go, amber for careful, red for stop', () => {
    expect(theme('go', 'base').tone).toBe('up');
    expect(theme('careful', 'brake').tone).toBe('warn');
    expect(theme('stop', 'loss_limit').tone).toBe('down');
    expect(theme('stop', 'streak').label).toBe('STOP FOR TODAY');
  });
  it('treats a reached target as a happy stop and a missing balance as neutral', () => {
    const t = theme('stop', 'target');
    expect(t.tone).toBe('up');
    expect(t.label).toContain('STOP');
    expect(theme('stop', 'no_balance').tone).toBe('mut');
  });
});

describe('meterLevel', () => {
  it('goes green, amber, red as the limit fills up', () => {
    expect(meterLevel(0)).toBe('ok');
    expect(meterLevel(0.49)).toBe('ok');
    expect(meterLevel(0.5)).toBe('near');
    expect(meterLevel(0.79)).toBe('near');
    expect(meterLevel(0.8)).toBe('hot');
    expect(meterLevel(1)).toBe('hot');
    expect(meterLevel(null)).toBe('ok');
  });
});

describe('streakText', () => {
  it('words the streak', () => {
    expect(streakText({ kind: null, n: 0 })).toBe('No streak yet');
    expect(streakText({ kind: 'loss', n: 1 })).toBe('1 loss in a row');
    expect(streakText({ kind: 'loss', n: 3 })).toBe('3 losses in a row');
    expect(streakText({ kind: 'win', n: 1 })).toBe('1 win in a row');
    expect(streakText({ kind: 'win', n: 4 })).toBe('4 wins in a row');
  });
});

describe('dots', () => {
  const r = (i: number, status: MmRecent['status']): MmRecent => ({ id: `t${i}`, status, profit: 1, amount: 1, symbol: i % 2 ? 'EURUSD_otc' : null, t: i });
  it('keeps the latest ones in order with the right colours', () => {
    const d = dots([r(1, 'win'), r(2, 'loss'), r(3, 'draw')]);
    expect(d.map((x) => x.cls)).toEqual(['bg-up', 'bg-down', 'bg-mut']);
    expect(d[0].label).toBe('Won EURUSD OTC');
    expect(d[1].label).toBe('Lost');
    expect(dots(Array.from({ length: 30 }, (_, i) => r(i, 'win')), 20)).toHaveLength(20);
    expect(dots(Array.from({ length: 30 }, (_, i) => r(i, 'win')), 20)[19].id).toBe('t29');
  });
});

describe('clampSettings', () => {
  it('keeps the same limits as the server', () => {
    const c = clampSettings({ ...DEFAULT_SETTINGS, base_pct: 99, max_pct: 0.01, min_stake: -5, loss: { on: true, pct: 9999 }, streak: { on: true, max: 1 }, recovery: { on: true, spread: 99 } });
    expect(c.base_pct).toBe(10);
    expect(c.max_pct).toBe(10);
    expect(c.min_stake).toBe(0.01);
    expect(c.loss.pct).toBe(50);
    expect(c.streak.max).toBe(2);
    expect(c.recovery.spread).toBe(6);
  });
  it('never lets the cap sit below the base stake, and leaves good settings alone', () => {
    expect(clampSettings({ ...DEFAULT_SETTINGS, base_pct: 2.5, max_pct: 1 }).max_pct).toBe(2.5);
    expect(clampSettings(DEFAULT_SETTINGS)).toEqual(DEFAULT_SETTINGS);
    expect(clampSettings({ ...DEFAULT_SETTINGS, base_pct: NaN }).base_pct).toBe(1);
  });
});

describe('small helpers', () => {
  it('compares settings', () => {
    expect(sameSettings(DEFAULT_SETTINGS, { ...DEFAULT_SETTINGS })).toBe(true);
    expect(sameSettings(DEFAULT_SETTINGS, { ...DEFAULT_SETTINGS, base_pct: 2 })).toBe(false);
    expect(sameSettings(null, DEFAULT_SETTINGS)).toBe(false);
  });
  it('works out a share of the balance', () => {
    expect(share(1000, 1.5)).toBe(15);
    expect(share(null, 1)).toBeNull();
  });
  it('shows a stake as a share of the balance', () => {
    expect(stakePct(13, 1000)).toBe('1.3%');
    expect(stakePct(300, 1000)).toBe('30%');
    expect(stakePct(null, 1000)).toBe('–');
    expect(stakePct(5, null)).toBe('–');
  });
});

describe('presets', () => {
  it('are valid settings the server would keep unchanged', () => {
    for (const p of PRESETS) {
      const full = { ...DEFAULT_SETTINGS, ...p.s };
      expect(clampSettings(full)).toEqual(full);
      expect(presetOf(full)).toBe(p.key);
    }
  });
  it('the balanced preset is the default, and an edited form matches none', () => {
    expect(presetOf(DEFAULT_SETTINGS)).toBe('balanced');
    expect(presetOf({ ...DEFAULT_SETTINGS, base_pct: 1.2 })).toBeNull();
    expect(presetOf({ ...DEFAULT_SETTINGS, account: 'demo', min_stake: 5 })).toBe('balanced');
  });
  it('every preset keeps the cap above the base stake and stays low-risk', () => {
    for (const p of PRESETS) {
      expect(p.s.max_pct).toBeGreaterThanOrEqual(p.s.base_pct);
      expect(p.s.max_pct).toBeLessThanOrEqual(5);
    }
  });
});
