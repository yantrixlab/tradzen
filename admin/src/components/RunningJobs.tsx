import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { elapsedText, headline, Job, JobsAnswer, looksStuck, percent, withoutJob } from '../lib/jobs';

/**
 * "Something is running": a badge at the bottom of the menu that shows up only while a background job is going (research, AI training, risk analysis, Edge Lab, backtests, sweeps ...),
 * and a panel with every running job - how far it is, for how long, a link to its page, and a Stop button. Each account sees only the jobs it may manage (backend/app/jobs.py).
 */
export const useJobs = (enabled = true) =>
  useQuery({ queryKey: ['jobs'], enabled, queryFn: () => api<JobsAnswer>('/api/jobs'), refetchInterval: 3000, retry: false });

const Spinner = ({ size = 16 }: { size?: number }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none">
    <circle cx="12" cy="12" r="9" opacity="0.25" /><path d="M21 12a9 9 0 0 0-9-9" />
  </svg>
);

function Bar({ value }: { value: number | null }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line/70" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined} aria-label="Progress">
      {value === null
        ? <div className="h-full w-1/3 animate-pulse rounded-full bg-acc/70 motion-reduce:animate-none" />
        : <div className="h-full rounded-full bg-acc transition-[width] duration-500" style={{ width: `${Math.max(3, value)}%` }} />}
    </div>);
}

function JobRow({ job, now, busy, confirm, onAsk, onCancel, onStop }: { job: Job; now: number; busy: boolean; confirm: boolean; onAsk: () => void; onCancel: () => void; onStop: () => void }) {
  const pct = percent(job.progress);
  const stuck = looksStuck(job, now);
  const stopping = job.detail === 'stopping…';
  return (
    <li className="rounded-lg border border-line bg-panel p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{job.title}</div>
          {job.detail && <div className="note truncate">{job.detail}</div>}
        </div>
        {pct !== null && <div className="shrink-0 text-sm font-bold tabular-nums text-acc">{pct}%</div>}
      </div>
      <div className="mt-2"><Bar value={pct} /></div>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="note">{elapsedText(job.started, now)}</span>
        {stuck && <span className="chip border-warn/40 bg-warn/10 text-warn" title="Far longer than a normal run">taking long</span>}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <NavLink to={job.page} className="btn btn-sm">Open</NavLink>
        {job.can_stop && !confirm && <button type="button" className="btn btn-sm btn-danger" disabled={busy || stopping} onClick={onAsk}>{stopping ? 'Stopping…' : 'Stop'}</button>}
        {job.can_stop && confirm && (
          <>
            <span className="note">Stop it now?</span>
            <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={onStop}>{busy ? 'Stopping…' : 'Yes, stop'}</button>
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onCancel}>Keep running</button>
          </>)}
        {!job.can_stop && <span className="note">cannot be stopped</span>}
      </div>
    </li>);
}

/** The badge + panel at the bottom of the menu. Nothing is drawn while nothing is running. */
export default function RunningJobs({ rail }: { rail: boolean }) {
  const qc = useQueryClient();
  const { data } = useJobs();
  const jobs = data?.jobs ?? [];
  const [open, setOpen] = useState(false);
  const [ask, setAsk] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);
  const skew = data ? data.now - Date.now() / 1000 : 0;                        // the server's clock, so the running time is right even if this computer's clock is off
  const now = Date.now() / 1000 + skew;
  void tick;

  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);            // the running time ticks every second while the panel is open
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => { window.clearInterval(t); document.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey); };
  }, [open]);
  useEffect(() => { if (!jobs.length) { setOpen(false); setAsk(null); } }, [jobs.length]);

  const stop = useMutation({
    mutationFn: (job: Job) => api<{ ok: boolean; result: string; jobs: Job[] }>(`/api/jobs/${encodeURIComponent(job.kind)}/${encodeURIComponent(job.ref)}/stop`, { method: 'POST', body: {} }),
    onSuccess: (res, job) => {
      qc.setQueryData<JobsAnswer>(['jobs'], (old) => ({ now: old?.now ?? Date.now() / 1000, jobs: res.jobs ?? withoutJob(old?.jobs ?? [], job.key) }));
      setAsk(null);
      qc.invalidateQueries();                                                  // the page of that job (research list, AI status, backtest runs ...) shows the new state at once
    },
  });

  if (!jobs.length) return null;
  const label = headline(jobs.length);
  return (
    <div ref={box} className="relative px-2 pb-2">
      <button type="button" aria-expanded={open} aria-controls="running-jobs" onClick={() => setOpen(!open)} title={rail ? label : 'See what is running and stop it'}
        className={`relative flex min-h-[44px] w-full items-center gap-2.5 rounded-lg border border-acc/50 bg-acc/10 px-3 py-2 text-left text-sm font-semibold text-[#c4b5fd] transition hover:bg-acc/20 lg:min-h-0 ${rail ? 'lg:justify-center lg:px-0' : ''}`}>
        <Spinner />
        <span className={`min-w-0 flex-1 truncate ${rail ? 'lg:hidden' : ''}`}>{label}</span>
        <span className={`grid h-5 min-w-[1.25rem] place-items-center rounded-full bg-acc px-1 text-[11px] font-extrabold text-white ${rail ? 'lg:absolute lg:-right-1 lg:-top-1 lg:h-4 lg:min-w-[1rem] lg:text-[9px]' : ''}`} aria-hidden="true">{jobs.length}</span>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`shrink-0 transition-transform ${open ? '' : 'rotate-180'} ${rail ? 'lg:hidden' : ''}`}><path d="M6 9l6 6 6-6" /></svg>
      </button>
      <span className="sr-only" role="status" aria-live="polite">{label}</span>
      {open && (
        <section id="running-jobs" aria-label="Running jobs"
          className="absolute bottom-full left-2 right-2 z-50 mb-2 max-h-[70vh] overflow-y-auto rounded-xl border border-line bg-panel2 p-3 shadow-2xl lg:bottom-2 lg:left-full lg:right-auto lg:mb-0 lg:ml-2 lg:w-80">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-mut">Running now</div>
            <button type="button" className="grid h-7 w-7 place-items-center rounded text-mut hover:text-fg" aria-label="Close" onClick={() => setOpen(false)}>✕</button>
          </div>
          <ul className="space-y-2">
            {jobs.map((j) => (
              <JobRow key={j.key} job={j} now={now} busy={stop.isPending && stop.variables?.key === j.key} confirm={ask === j.key}
                onAsk={() => { stop.reset(); setAsk(j.key); }} onCancel={() => setAsk(null)} onStop={() => stop.mutate(j)} />))}
          </ul>
          {stop.isError && <div className="mt-2 rounded-lg border border-down/40 bg-down/10 p-2 text-xs text-down" role="alert">{(stop.error as Error).message}</div>}
          <p className="note mt-2">Stopping ends the job at once; its page shows it as stopped. A backtest or sweep finishes its current step first.</p>
        </section>)}
    </div>);
}

/** The phone's top bar: a small spinner + count that opens the menu (where the panel is). Nothing while nothing is running. */
export function RunningChip({ onOpen }: { onOpen: () => void }) {
  const { data } = useJobs();
  const n = data?.jobs.length ?? 0;
  if (!n) return null;
  return (
    <button type="button" onClick={onOpen} aria-label={`${headline(n)}: open the menu to see and stop them`}
      className="mr-1 inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-acc/50 bg-acc/10 px-2.5 text-xs font-bold text-[#c4b5fd]"><Spinner size={14} />{n}</button>);
}
