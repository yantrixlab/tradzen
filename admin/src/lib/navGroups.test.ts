import { describe, expect, it } from 'vitest';
import { buildGroups, filterItems, groupOf, isOpen, matches, NavItem, pinned, restorable, togglePin, MAX_PINS } from './navGroups';

const item = (to: string, label = to): NavItem => ({ to, label, icon: '•' });
const ALL = ['/', '/markets', '/chart', '/sessions', '/session', '/live', '/generate', '/trades', '/guard', '/style', '/signals', '/library', '/strategies', '/backtests', '/sweeps', '/clinic',
  '/research', '/edgelab', '/ai', '/data', '/alerts', '/system', '/users', '/features', '/payments', '/plans', '/account'].map((p) => item(p));

describe('buildGroups', () => {
  it('puts every page of the full menu into exactly one place', () => {
    const b = buildGroups(ALL);
    const seen = [...b.top, ...b.groups.flatMap((g) => g.items)].map((i) => i.to);
    expect(seen.sort()).toEqual(ALL.map((i) => i.to).sort());
    expect(new Set(seen).size).toBe(seen.length);
    expect(b.top.map((i) => i.to)).toEqual(['/']);
    expect(b.groups.map((g) => g.id)).toEqual(['trade', 'mine', 'strategy', 'research', 'admin', 'account']);
  });

  it('leaves out a group the person has nothing in, and keeps the menu order inside a group', () => {
    const b = buildGroups([item('/trades'), item('/markets'), item('/account'), item('/plans')]);
    expect(b.groups.map((g) => g.id)).toEqual(['trade', 'mine', 'account']);
    expect(b.groups[2].items.map((i) => i.to)).toEqual(['/plans', '/account']);
    expect(b.top).toEqual([]);
  });

  it('a page nobody grouped yet goes to "More" instead of vanishing', () => {
    const b = buildGroups([item('/trades'), item('/brand-new')]);
    expect(b.groups[b.groups.length - 1]).toMatchObject({ id: 'more', items: [{ to: '/brand-new' }] });
  });
});

describe('open state, active group and pins', () => {
  const b = buildGroups(ALL);
  it('a group follows what the person chose, else its default', () => {
    const [trade, , strategy] = b.groups;
    expect(isOpen(trade, undefined)).toBe(true);
    expect(isOpen(strategy, undefined)).toBe(false);
    expect(isOpen(trade, { trade: false })).toBe(false);
    expect(isOpen(strategy, { strategy: true })).toBe(true);
  });

  it('knows which group a page, or a sub-page of it, is in', () => {
    expect(groupOf(b.groups, '/backtests')).toBe('strategy');
    expect(groupOf(b.groups, '/strategies/12/edit')).toBe('strategy');
    expect(groupOf(b.groups, '/')).toBeNull();
    expect(groupOf(b.groups, '/nowhere')).toBeNull();
    expect(matches('/', '/strategies')).toBe(false);
    expect(matches('/session', '/sessions')).toBe(false);
  });

  it('pins keep their order, drop pages the person can no longer open, and are capped', () => {
    const few = ALL.filter((i) => i.to !== '/chart');
    expect(pinned(few, ['/trades', '/chart', '/markets']).map((i) => i.to)).toEqual(['/trades', '/markets']);
    expect(pinned(ALL, undefined)).toEqual([]);
    expect(togglePin(['/a'], '/b')).toEqual(['/a', '/b']);
    expect(togglePin(['/a', '/b'], '/a')).toEqual(['/b']);
    const full = Array.from({ length: MAX_PINS }, (_, i) => `/p${i}`);
    expect(togglePin(full, '/new')).toEqual([...full.slice(1), '/new']);
  });
});

describe('find a page and remember the last page', () => {
  const b = buildGroups(ALL.map((i) => ({ ...i, label: { '/backtests': 'Backtest lab', '/strategies': 'Strategies', '/library': 'Strategy library', '/trades': 'My trades' }[i.to] ?? i.to })));
  it('matches every typed word in any order and drops empty groups', () => {
    const f = filterItems(b, 'strat');
    expect(f.groups.map((g) => g.id)).toEqual(['strategy']);
    expect(f.groups[0].items.map((i) => i.to)).toEqual(['/library', '/strategies']);
    expect(filterItems(b, 'lab back').groups[0].items.map((i) => i.to)).toEqual(['/backtests']);
    expect(filterItems(b, 'zzz')).toEqual({ top: [], groups: [] });
    expect(filterItems(b, '  ')).toBe(b);
  });

  it('remembers a sub-page as its list, and an unknown page not at all', () => {
    expect(restorable(ALL, '/strategies/12/edit')).toBe('/strategies');
    expect(restorable(ALL, '/backtests')).toBe('/backtests');
    expect(restorable(ALL, '/')).toBe('/');
    expect(restorable(ALL, '/nowhere')).toBeNull();
  });
});
