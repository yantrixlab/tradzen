/** The running-jobs list behind the menu's "running" indicator (backend/app/jobs.py). Pure helpers, unit-tested in jobs.test.ts; the component is components/RunningJobs.tsx. */

export interface Job {
  key: string;                 // "research:18": what to stop
  kind: string;                // research | ai | risk | edgelab | backtest | sweep | marketdata
  ref: string;
  title: string;
  page: string;                // the page that shows this job in full
  started: number | null;      // seconds since 1970, server clock
  progress: number | null;     // 0..1, null = the job cannot say how far it is
  detail: string;
  can_stop: boolean;
}
export interface JobsAnswer { now: number; jobs: Job[] }

/** Whole percent for a progress bar, or null when the job does not report one. */
export function percent(p: number | null | undefined): number | null {
  if (p === null || p === undefined || !Number.isFinite(p)) return null;
  return Math.round(Math.max(0, Math.min(1, p)) * 100);
}

/** "just started", "42 s", "3 min 05 s", "2 h 10 min": how long a job has been running, on the server's clock. */
export function elapsedText(started: number | null | undefined, now: number): string {
  if (started === null || started === undefined) return '';
  const s = Math.max(0, Math.floor(now - started));
  if (s < 5) return 'just started';
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, '0')} min`;
}

/** The short label of the indicator. */
export const headline = (n: number): string => (n <= 0 ? 'Nothing running' : `${n} running`);

/** A job running far longer than a normal run is worth a second look: a hint for the panel. */
export const looksStuck = (j: Job, now: number, limitMin = 90): boolean => j.started !== null && now - j.started > limitMin * 60 && j.kind !== 'marketdata';

/** The jobs after a stop request: the server's fresh list when it sent one, otherwise the old list without the stopped job. */
export function withoutJob(jobs: Job[], key: string): Job[] {
  return jobs.filter((j) => j.key !== key);
}
