import { useMemo, useState } from 'react';

/** Click a column title to sort by it: first click one way, again the other way. */
export interface SortState { key: string; desc: boolean }

export function SortTh({ label, k, sort, onSort, className = '', title }: { label: string; k?: string; sort: SortState | null; onSort: (key: string) => void; className?: string; title?: string }) {
  if (!k) return <th className={`th ${className}`}>{label}</th>;
  const on = sort?.key === k;
  return (
    <th className={`th ${className}`} aria-sort={on ? (sort!.desc ? 'descending' : 'ascending') : 'none'}>
      <button type="button" title={title ?? `Sort by ${label.toLowerCase()}`} onClick={() => onSort(k)}
        className={`group inline-flex items-center gap-1 uppercase tracking-wide transition hover:text-fg focus-visible:text-fg focus-visible:outline-none ${on ? 'text-fg' : ''}`}>
        {label}
        <span aria-hidden="true" className={`text-[9px] leading-none ${on ? 'text-acc' : 'text-mut/50 group-hover:text-mut'}`}>{on ? (sort!.desc ? '▼' : '▲') : '↕'}</span>
      </button>
    </th>
  );
}

/** Sort state with the usual cycle: a new column starts in the `firstDesc` direction, a second click flips it. */
export function useSortState(initial: SortState | null = null, firstDesc: (key: string) => boolean = () => false): [SortState | null, (key: string) => void, (s: SortState | null) => void] {
  const [sort, setSort] = useState<SortState | null>(initial);
  const toggle = (key: string) => setSort((s) => (s?.key === key ? { key, desc: !s.desc } : { key, desc: firstDesc(key) }));
  return [sort, toggle, setSort];
}

type Value = string | number | null | undefined;

/** Rows ordered by one of their values (empty values always last). `get` maps a column key to the row's value. */
export function sortRows<T>(rows: T[], sort: SortState | null, get: (row: T, key: string) => Value): T[] {
  if (!sort) return rows;
  const empty = (v: Value) => v === null || v === undefined || v === '';
  const have = rows.filter((r) => !empty(get(r, sort.key)));
  const none = rows.filter((r) => empty(get(r, sort.key)));
  const cmp = (a: Value, b: Value) => (typeof a === 'string' || typeof b === 'string' ? String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' }) : (a as number) - (b as number));
  return [...have].sort((a, b) => (sort.desc ? -1 : 1) * cmp(get(a, sort.key), get(b, sort.key))).concat(none);
}

export function useSorted<T>(rows: T[], sort: SortState | null, get: (row: T, key: string) => Value): T[] {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => sortRows(rows, sort, get), [rows, sort]);
}

/** On a phone the table turns into cards and its header row is hidden, so the same choice is offered as a small "Sort by" control. */
export function SortSelect({ options, sort, onChange }: { options: [string, string][]; sort: SortState | null; onChange: (s: SortState | null) => void }) {
  return (
    <div className="flex items-center gap-1 md:hidden">
      <select className="input !w-auto" aria-label="Sort by" value={sort?.key ?? ''} onChange={(e) => onChange(e.target.value ? { key: e.target.value, desc: sort?.desc ?? true } : null)}>
        <option value="">Sort: newest first</option>
        {options.map(([k, l]) => <option key={k} value={k}>Sort: {l}</option>)}
      </select>
      <button type="button" className="btn btn-sm" aria-label={sort?.desc ? 'Descending, tap for ascending' : 'Ascending, tap for descending'} disabled={!sort} onClick={() => sort && onChange({ ...sort, desc: !sort.desc })}>{sort?.desc === false ? '▲' : '▼'}</button>
    </div>
  );
}
