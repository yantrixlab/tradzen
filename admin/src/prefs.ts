import { useCallback, useSyncExternalStore } from 'react';
import { api } from './api';

/**
 * The signed-in person's display preferences (which chart layers are on, the indicators picked, ...), kept on the server per account so they follow the person
 * to another browser, with a copy in this browser so the page paints right away and still works offline. Changes are saved a moment after the last tap.
 *   const [zoneText, setZoneText] = usePref('chart.zone-text', true);
 */
const CACHE = 'qxs-prefs-v1';
let store: Record<string, unknown> = (() => { try { return JSON.parse(localStorage.getItem(CACHE) ?? '{}') as Record<string, unknown>; } catch { return {}; } })();
let loaded = false, loading = false, timer: ReturnType<typeof setTimeout> | undefined;
let pending: Record<string, unknown> = {};
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
const cache = () => { try { localStorage.setItem(CACHE, JSON.stringify(store)); } catch { /* not cached */ } };

function load() {
  if (loaded || loading) return;
  loading = true;
  api<{ prefs: Record<string, unknown> }>('/api/prefs')
    .then((r) => { store = { ...r.prefs, ...pending }; loaded = true; cache(); emit(); })          // the server's copy wins, except for what was changed a moment ago
    .catch(() => { /* signed out or offline: this browser's copy is used */ })
    .finally(() => { loading = false; });
}

function flush() {
  timer = undefined;
  const changes = pending;
  pending = {};
  if (!Object.keys(changes).length) return;
  api('/api/prefs', { method: 'PUT', body: { prefs: changes } }).catch(() => { pending = { ...changes, ...pending }; });       // try again with the next change
}

export function setPref(key: string, value: unknown) {
  store = { ...store, [key]: value };
  pending = { ...pending, [key]: value };
  cache();
  emit();
  if (timer) clearTimeout(timer);
  timer = setTimeout(flush, 600);
}

/** Forget what the previous account set in this browser (called when somebody signs out). */
export function clearPrefs() {
  store = {}; pending = {}; loaded = false;
  try { localStorage.removeItem(CACHE); } catch { /* ignore */ }
  emit();
}

export function usePref<T>(key: string, def: T): [T, (v: T) => void] {
  const raw = useSyncExternalStore((f) => { subs.add(f); load(); return () => { subs.delete(f); }; }, () => store[key]);
  const set = useCallback((v: T) => setPref(key, v), [key]);
  return [raw === undefined ? def : (raw as T), set];
}
