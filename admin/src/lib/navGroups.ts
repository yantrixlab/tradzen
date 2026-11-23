/** Grouping of the sidebar menu. Pure functions (unit-tested in navGroups.test.ts); the component is components/SideNav.tsx and the pages themselves are listed in App.tsx. */

export interface NavItem { to: string; label: string; icon: string }
export interface NavGroup { id: string; label: string; icon: string; items: NavItem[]; open: boolean }

/** The groups, in menu order. `open` = whether the group starts open for somebody who has not chosen yet. A page that is not listed here lands in "More". */
export const GROUP_DEFS: { id: string; label: string; icon: string; open: boolean; paths: string[] }[] = [
  { id: 'trade', label: 'Trade', icon: '⚡', open: true, paths: ['/markets', '/chart', '/sessions', '/session', '/live', '/generate'] },
  { id: 'mine', label: 'My trading', icon: '$', open: true, paths: ['/trades', '/moneymgmt', '/guard', '/style', '/signals'] },
  { id: 'strategy', label: 'Strategies', icon: '⚙', open: false, paths: ['/library', '/strategies', '/backtests', '/sweeps', '/clinic'] },
  { id: 'research', label: 'Research & data', icon: '◎', open: false, paths: ['/research', '/edgelab', '/ai', '/data'] },
  { id: 'admin', label: 'Administration', icon: '⌘', open: false, paths: ['/alerts', '/system', '/users', '/features', '/payments'] },
  { id: 'account', label: 'Plan & account', icon: '☺', open: false, paths: ['/plans', '/account'] },
];
/** Shown above the groups, never inside one. */
export const TOP_PATHS = ['/'];
export const PINNED_ID = 'pinned';
export const MAX_PINS = 8;

export interface Built { top: NavItem[]; groups: NavGroup[] }

/** Splits the menu the person may use into the top items and the groups; a group with nothing in it for this person is left out. */
export function buildGroups(items: NavItem[]): Built {
  const byPath = new Map(items.map((i) => [i.to, i]));
  const placed = new Set<string>(TOP_PATHS);
  const top = TOP_PATHS.map((p) => byPath.get(p)).filter((i): i is NavItem => !!i);
  const groups: NavGroup[] = [];
  for (const g of GROUP_DEFS) {
    const its = g.paths.map((p) => byPath.get(p)).filter((i): i is NavItem => !!i);
    its.forEach((i) => placed.add(i.to));
    if (its.length) groups.push({ id: g.id, label: g.label, icon: g.icon, open: g.open, items: its });
  }
  const rest = items.filter((i) => !placed.has(i.to));
  if (rest.length) groups.push({ id: 'more', label: 'More', icon: '…', open: false, items: rest });
  return { top, groups };
}

/** Does this location belong to the menu entry (`/strategies/12/edit` belongs to `/strategies`; `/` only matches itself)? */
export const matches = (to: string, path: string): boolean => (to === '/' ? path === '/' : path === to || path.startsWith(to + '/'));

/** The group the page belongs to, or null (the top items, an unknown page). */
export function groupOf(groups: NavGroup[], path: string): string | null {
  return groups.find((g) => g.items.some((i) => matches(i.to, path)))?.id ?? null;
}

export const isOpen = (g: NavGroup, saved: Record<string, boolean> | undefined): boolean => saved?.[g.id] ?? g.open;

/** Pinned pages that still exist for this person, in the order they were pinned, at most MAX_PINS. */
export function pinned(items: NavItem[], pins: string[] | undefined): NavItem[] {
  const byPath = new Map(items.map((i) => [i.to, i]));
  return (pins ?? []).map((p) => byPath.get(p)).filter((i): i is NavItem => !!i).slice(0, MAX_PINS);
}

export function togglePin(pins: string[] | undefined, to: string): string[] {
  const list = pins ?? [];
  if (list.includes(to)) return list.filter((p) => p !== to);
  return [...list, to].slice(-MAX_PINS);                       // the newest pin wins when the list is full
}

/** What the "find a page" box shows: every entry whose name contains the words typed (all of them, any order), grouped as in the menu. */
export function filterItems(built: Built, query: string): Built {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return built;
  const hit = (i: NavItem) => words.every((w) => i.label.toLowerCase().includes(w));
  return { top: built.top.filter(hit), groups: built.groups.map((g) => ({ ...g, items: g.items.filter(hit) })).filter((g) => g.items.length) };
}

/** The menu entry a location counts as, for remembering "the page I was on": a sub-page (`/strategies/12/edit`) is remembered as its list (`/strategies`), nothing else is. */
export function restorable(items: NavItem[], path: string): string | null {
  return items.find((i) => matches(i.to, path))?.to ?? null;
}
