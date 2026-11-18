/** Calendar ranges for the "My trades" page, worked out on the viewer's own clock (their midnights, not the server's).
 *  A range is [from, to) in epoch seconds; `to` null = up to now, `from` null = from the very beginning. */
export interface RangeSpan { from: number | null; to: number | null }

export const PRESETS: { key: string; label: string }[] = [
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: '7d', label: 'Last 7 days' },
  { key: 'week', label: 'This week' },
  { key: 'lastweek', label: 'Last week' },
  { key: '30d', label: 'Last 30 days' },
  { key: 'month', label: 'This month' },
  { key: 'lastmonth', label: 'Last month' },
  { key: '90d', label: 'Last 90 days' },
  { key: 'all', label: 'All time' },
  { key: 'custom', label: 'Custom dates' },
];

const sec = (d: Date) => Math.floor(d.getTime() / 1000);
const midnight = (now: Date, backDays = 0) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - backDays);

/** `YYYY-MM-DD` of a local date (what an <input type="date"> holds) */
export const ymd = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
/** local midnight of a `YYYY-MM-DD` string; null when it is not a date */
export const fromYmd = (s: string): Date | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
};

/** The range of a preset. Weeks start on Monday. `custom` uses the two dates (inclusive of the last day). */
export function rangeOf(key: string, now: Date = new Date(), custom?: { from: string; to: string }): RangeSpan {
  const today = midnight(now);
  const weekStart = midnight(now, (now.getDay() + 6) % 7);
  switch (key) {
    case 'today': return { from: sec(today), to: null };
    case 'yesterday': return { from: sec(midnight(now, 1)), to: sec(today) };
    case '7d': return { from: sec(midnight(now, 6)), to: null };
    case 'week': return { from: sec(weekStart), to: null };
    case 'lastweek': return { from: sec(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() - 7)), to: sec(weekStart) };
    case '30d': return { from: sec(midnight(now, 29)), to: null };
    case 'month': return { from: sec(new Date(now.getFullYear(), now.getMonth(), 1)), to: null };
    case 'lastmonth': return { from: sec(new Date(now.getFullYear(), now.getMonth() - 1, 1)), to: sec(new Date(now.getFullYear(), now.getMonth(), 1)) };
    case '90d': return { from: sec(midnight(now, 89)), to: null };
    case 'custom': {
      const a = fromYmd(custom?.from ?? ''), b = fromYmd(custom?.to ?? '');
      return { from: a ? sec(a) : null, to: b ? sec(new Date(b.getFullYear(), b.getMonth(), b.getDate() + 1)) : null };
    }
    default: return { from: null, to: null };
  }
}

/** a short sentence for the page: "Mon, 17 Nov 2026 - Tue, 18 Nov 2026" */
export function describeRange(span: RangeSpan, now: Date = new Date()): string {
  const f = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  if (span.from === null && span.to === null) return 'every trade recorded';
  const a = span.from === null ? 'the beginning' : f(span.from);
  const b = span.to === null ? f(sec(now)) : f(span.to - 1);
  return a === b ? a : `${a} - ${b}`;
}
