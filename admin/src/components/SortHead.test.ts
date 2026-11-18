import { describe, expect, it } from 'vitest';
import { sortRows } from './SortHead';

const rows = [{ n: 'b', v: 2 }, { n: 'a', v: null }, { n: 'c', v: 10 }, { n: 'B2', v: 1 }];
const get = (r: (typeof rows)[number], k: string) => (k === 'n' ? r.n : r.v);

describe('sortRows', () => {
  it('sorts numbers by value, not as text, and keeps empty values last in both directions', () => {
    expect(sortRows(rows, { key: 'v', desc: false }, get).map((r) => r.n)).toEqual(['B2', 'b', 'c', 'a']);
    expect(sortRows(rows, { key: 'v', desc: true }, get).map((r) => r.n)).toEqual(['c', 'b', 'B2', 'a']);
  });
  it('sorts text without caring about case', () => {
    expect(sortRows(rows, { key: 'n', desc: false }, get).map((r) => r.n)).toEqual(['a', 'b', 'B2', 'c']);
  });
  it('no sort keeps the order and does not change the original list', () => {
    expect(sortRows(rows, null, get)).toBe(rows);
    sortRows(rows, { key: 'v', desc: true }, get);
    expect(rows[0].n).toBe('b');
  });
});
