import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from 'react';
import type { Op } from '../lib/conditions';
import type { IndicatorForm } from '../lib/specModel';

/** What the chart is currently shading: one condition (a whole CALL/PUT group or a single clause). */
export interface Hl { key: string; label: string; tone: 'call' | 'put' | 'clause'; steps?: string[] }
export type Payload = { t: 'ref'; name: string } | { t: 'num'; value: string } | { t: 'op'; op: Op } | { t: 'recipe'; id: string } | { t: 'indicator'; type: string } | { t: 'candle'; id: string } | { t: 'pattern'; id: string };
export interface NameGroup { label: string; items: { name: string; text: string; color?: string }[] }
export type Marks = Record<string, { hits: boolean[]; error: string | null; spans?: Record<string, [number, number, number][]> }>;

interface Ctx {
  hl: Hl | null; hover: (h: Hl | null) => void; pinned: boolean; pin: (h: Hl) => void; unpin: () => void; pinnedKey: string | null;
  groups: Hl[]; marks: Marks; n: number;
  drag: Payload | null; setDrag: (p: Payload | null) => void;
  indicators: IndicatorForm[]; names: NameGroup[];
  active: number; setActive: (i: number) => void;
}

const C = createContext<Ctx | null>(null);
export const useBuilder = (): Ctx => {
  const c = useContext(C);
  if (!c) throw new Error('useBuilder outside BuilderProvider');
  return c;
};

export function BuilderProvider({ children, marks, n, groups, indicators, names, active, setActive }: {
  children: ReactNode; marks: Marks; n: number; groups: Hl[]; indicators: IndicatorForm[]; names: NameGroup[]; active: number; setActive: (i: number) => void;
}) {
  const [hovered, setHovered] = useState<Hl | null>(null);
  const [pinnedHl, setPinned] = useState<Hl | null>(null);
  const [drag, setDragState] = useState<Payload | null>(null);
  const hover = useCallback((h: Hl | null) => setHovered(h), []);
  const pin = useCallback((h: Hl) => setPinned((p) => (p && p.key === h.key ? null : h)), []);
  const unpin = useCallback(() => setPinned(null), []);
  // a drag changes the DOM; start tracking one tick later so the browser does not cancel it
  const setDrag = useCallback((p: Payload | null) => { if (p) setTimeout(() => setDragState(p), 0); else setDragState(null); }, []);
  const value = useMemo<Ctx>(() => ({
    hl: pinnedHl ?? hovered, hover, pinned: !!pinnedHl, pin, unpin, pinnedKey: pinnedHl?.key ?? null,
    groups, marks, n, drag, setDrag, indicators, names, active, setActive,
  }), [pinnedHl, hovered, hover, pin, unpin, groups, marks, n, drag, setDrag, indicators, names, active, setActive]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

export const encode = (p: Payload) => JSON.stringify(p);
export const decode = (s: string): Payload | null => { try { return JSON.parse(s) as Payload; } catch { return null; } };
