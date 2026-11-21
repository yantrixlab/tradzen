import { useEffect, useRef, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { usePref } from '../prefs';
import { buildGroups, filterItems, groupOf, isOpen, NavGroup, NavItem, PINNED_ID, pinned, togglePin } from '../lib/navGroups';

/**
 * The sidebar menu: pages in collapsible groups, a "find a page" box, pinned pages on top, and (on a desktop) an icon-only rail.
 * What the person chose (open groups, pins) is kept per account with usePref, so it comes back on the next visit and on another browser.
 * Opening a page inside a closed group opens that group once; closing it again afterwards is respected until the next time the page changes.
 */
interface Props { items: NavItem[]; live: boolean; rail: boolean }

const Chevron = ({ open }: { open: boolean }) => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}><path d="M9 6l6 6-6 6" /></svg>
);

export default function SideNav({ items, live, rail }: Props) {
  const loc = useLocation();
  const go = useNavigate();
  const [saved, setSaved] = usePref<Record<string, boolean>>('nav.open', {});
  const [pins, setPins] = usePref<string[]>('nav.pins', []);
  const [q, setQ] = useState('');
  const box = useRef<HTMLInputElement>(null);
  const built = buildGroups(items);
  const searching = q.trim() !== '';
  const shown = filterItems(built, q);
  const active = groupOf(built.groups, loc.pathname);
  const pins2 = pinned(items, pins);
  const pinnedOpen = saved[PINNED_ID] ?? true;

  useEffect(() => {                                                // arriving at a page inside a closed group opens that group
    const g = built.groups.find((x) => x.id === active);
    if (g && !isOpen(g, saved)) setSaved({ ...saved, [g.id]: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.pathname]);

  useEffect(() => {                                                // "/" jumps to the find box, like on most sites
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || (t && (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName) || t.isContentEditable))) return;
      if (!box.current || box.current.offsetParent === null) return;
      e.preventDefault();
      box.current.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const setOpen = (id: string, open: boolean) => setSaved({ ...saved, [id]: open });
  const setAll = (open: boolean) => setSaved(Object.fromEntries([...built.groups.map((g) => [g.id, open]), [PINNED_ID, open]]));
  const allOpen = built.groups.every((g) => isOpen(g, saved));

  const row = (it: NavItem, key: string) => {
    const isLive = it.to === '/sessions' && live;
    const isPinned = pins.includes(it.to);
    return (
      <div key={key} className="group/row relative flex items-center">
        <NavLink to={it.to} end={it.to === '/'} title={rail ? it.label : undefined}
          className={({ isActive }) => `flex min-h-[44px] min-w-0 flex-1 items-center gap-2.5 rounded-lg px-3 py-2 text-[15px] transition lg:min-h-0 lg:text-sm ${rail ? 'lg:justify-center lg:px-0' : ''} ${isLive ? `live-nav border border-rose-500/50 bg-rose-500/10 font-semibold ${isActive ? 'text-rose-100' : 'text-rose-200 hover:text-white'}` : isActive ? 'bg-acc/15 text-[#c4b5fd]' : 'text-mut hover:bg-panel hover:text-fg'}`}>
          {isLive ? <span className="grid w-4 place-items-center"><span className="live-dot h-2.5 w-2.5 rounded-full bg-rose-500" /></span> : <span className="w-4 text-center">{it.icon}</span>}
          <span className={`min-w-0 flex-1 truncate ${rail ? 'lg:hidden' : ''}`}>{it.label}</span>
          {isLive && <span className={`live-blink rounded bg-rose-500 px-1.5 py-px text-[9px] font-extrabold tracking-widest text-white ${rail ? 'lg:hidden' : ''}`}>LIVE</span>}
        </NavLink>
        <button type="button" onClick={() => setPins(togglePin(pins, it.to))} aria-pressed={isPinned} aria-label={`${isPinned ? 'Unpin' : 'Pin'} ${it.label}`} title={isPinned ? 'Unpin from the top' : 'Pin to the top'}
          className={`absolute right-1 grid h-8 w-8 place-items-center rounded-md text-base transition max-lg:opacity-100 ${rail ? 'lg:hidden' : ''} ${isPinned ? 'text-warn opacity-100' : 'text-mut opacity-0 hover:text-warn focus:opacity-100 group-hover/row:opacity-100'}`}>
          {isPinned ? '★' : '☆'}
        </button>
      </div>);
  };

  const header = (id: string, label: string, icon: string, count: number, open: boolean, dot: boolean) => (
    <button type="button" aria-expanded={open} aria-controls={`nav-g-${id}`} onClick={() => setOpen(id, !open)}
      className="flex min-h-[40px] w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-mut transition hover:bg-panel hover:text-fg lg:min-h-0">
      <Chevron open={open} />
      <span className="flex-1 truncate">{label}</span>
      {!open && dot && <span className="h-1.5 w-1.5 rounded-full bg-acc" title="The page you are on is in this group" />}
      <span className="rounded bg-panel px-1.5 py-px text-[10px] font-medium normal-case tracking-normal" aria-label={`${count} pages`}>{count}</span>
      <span className="sr-only">{icon}</span>
    </button>);

  const section = (g: NavGroup, list: NavItem[], open: boolean, forced = false) => (
    <div key={g.id} className={rail ? (g.id === PINNED_ID ? 'lg:hidden' : 'lg:border-t lg:border-line/70 lg:pt-1') : ''}>
      <div className={rail ? 'lg:hidden' : ''}>{forced ? <div className="px-3 pb-0.5 pt-2 text-[11px] font-semibold uppercase tracking-wider text-mut">{g.label}</div> : header(g.id, g.label, g.icon, list.length, open, active === g.id)}</div>
      <div id={`nav-g-${g.id}`} className={`space-y-0.5 ${open || forced ? '' : rail ? 'max-lg:hidden' : 'hidden'}`}>{list.map((it) => row(it, `${g.id}:${it.to}`))}</div>
    </div>);

  const pinGroup: NavGroup = { id: PINNED_ID, label: 'Pinned', icon: '★', items: pins2, open: true };
  const empty = searching && !shown.top.length && !shown.groups.length;

  return (
    <>
      <div className={`px-2 pb-2 ${rail ? 'lg:hidden' : ''}`}>
        <div className="relative">
          <input ref={box} value={q} onChange={(e) => setQ(e.target.value)} type="search" inputMode="search" enterKeyHint="go" autoComplete="off" spellCheck={false} aria-label="Find a page" placeholder="Find a page…  ( / )"
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setQ(''); box.current?.blur(); }
              if (e.key === 'Enter') { const first = [...shown.top, ...shown.groups.flatMap((g) => g.items)][0]; if (first) { go(first.to); setQ(''); box.current?.blur(); } }
            }}
            className="input h-9 w-full pr-8 text-sm" />
          {searching && <button type="button" aria-label="Clear" className="absolute right-1 top-1 grid h-7 w-7 place-items-center rounded text-mut hover:text-fg" onClick={() => { setQ(''); box.current?.focus(); }}>✕</button>}
        </div>
        {!searching && built.groups.length > 1 && (
          <div className="mt-1.5 flex justify-end px-1 text-[11px]">
            <button type="button" className="text-mut hover:text-fg" onClick={() => setAll(!allOpen)}>{allOpen ? 'Collapse all' : 'Expand all'}</button>
          </div>)}
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto overscroll-contain px-2 pb-2" aria-label="Pages">
        {empty && <div className="note px-3 py-2">No page matches “{q}”.</div>}
        {(searching ? shown.top : built.top).length > 0 && <div className="space-y-0.5">{(searching ? shown.top : built.top).map((it) => row(it, `top:${it.to}`))}</div>}
        {!searching && pins2.length > 0 && section(pinGroup, pins2, pinnedOpen)}
        {(searching ? shown.groups : built.groups).map((g) => section(g, g.items, isOpen(g, saved), searching))}
      </nav>
    </>
  );
}
