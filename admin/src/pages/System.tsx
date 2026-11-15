import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { ago, bytes, dateTime } from '../format';
import { useLiveState } from '../hooks';
import { Badge, Card, ErrorBox, PageTitle, Spinner, Stat } from '../components/ui';

interface Sys {
  db_path: string; db_bytes: number; wal_bytes: number; schema_version: number; counts: Record<string, number>; python: string; started: number | null; now: number;
  strategies_running: number; strategy_errors: Record<string, string[]>; ingest: { batches: number; ticks: number; last_recv: number | null };
}

interface BackupInfo { name: string; files: number; bytes: number }
interface Backups { nightly: boolean; hour_utc: number; keep: number; backups: BackupInfo[] }
interface MadeBackup { name: string; files: number; bytes: number; removed: number }

/** Backup folder names are YYYYMMDD-HHMMSS (UTC), with -2, -3 ... when two were made in the same second. */
const backupTime = (name: string): number | null => {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(name);
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 : null;
};

/** The databases are copied (main one + every user's private one) while the server keeps running; the newest few are kept on the server's disk. */
function BackupsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['backups'], queryFn: () => api<Backups>('/api/system/backups'), refetchInterval: 30000 });
  const make = useMutation({ mutationFn: () => api<MadeBackup>('/api/system/backup', { method: 'POST', body: {} }), onSuccess: () => qc.invalidateQueries({ queryKey: ['backups'] }) });
  const d = q.data;
  return (
    <Card title="Backups" className="mt-4"
      right={<button className="btn btn-primary" disabled={make.isPending} onClick={() => make.mutate()}>{make.isPending ? 'Backing up…' : 'Back up now'}</button>}>
      {q.isError && <ErrorBox error={q.error} />}
      {make.isSuccess && <div role="status" className="mb-3 rounded-lg border border-up/40 bg-up/10 px-3 py-2 text-sm text-up">Backup {make.data.name} done: {make.data.files} database{make.data.files === 1 ? '' : 's'}, {bytes(make.data.bytes)}.</div>}
      {make.isError && <div role="alert" className="mb-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{(make.error as Error).message}</div>}
      {d && (
        <>
          <p className="note mb-3">
            {d.nightly ? <>A backup is made automatically every night after <b className="text-fg">{String(d.hour_utc).padStart(2, '0')}:00 UTC</b>; the newest <b className="text-fg">{d.keep}</b> are kept.</> : <>The nightly backup is <b className="text-fg">off</b> (switch it on with QXS_BACKUP=on); the newest {d.keep} manual backups are kept.</>}
            {' '}A backup on the same disk does not protect against losing the server: copy the newest ones somewhere else from time to time (see DEPLOY.md).
          </p>
          {d.backups.length === 0 ? <div className="p-4 text-center text-mut">No backup yet.</div> : (
            <table className="w-full text-sm">
              <thead><tr>{['Backup', 'Made', 'Databases', 'Size'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{d.backups.map((b) => {
                const t = backupTime(b.name);
                return (
                  <tr key={b.name}><td className="td font-mono text-xs">{b.name}</td>
                    <td className="td text-mut" title={t ? dateTime(t) : ''}>{t ? ago(t, Date.now() / 1000) : ''}</td>
                    <td className="td tabular-nums">{b.files}</td><td className="td tabular-nums">{bytes(b.bytes)}</td></tr>);
              })}</tbody>
            </table>
          )}
        </>
      )}
    </Card>
  );
}

interface SeedInfo { counts: Record<string, number>; created: number | null; files: number; bytes: number; error?: string }
interface SeedState {
  pending: SeedInfo | null; fresh: boolean; server: Record<string, number>;
  merge: { state: 'idle' | 'running' | 'done' | 'error'; done: number; total: number; result: Record<string, number> | null; error: string | null };
}
const LABELS: Record<string, string> = { candles: 'Candles', sentiment: 'Sentiment rows', instruments: 'Pairs', signals: 'Signals', strategies: 'Strategies', backtest_runs: 'Backtest runs', broker_trades: 'Trades', style_signals: 'My style samples' };

/** Send the data collected elsewhere (the file made by `python -m app.seed make`) to this server: add its candles to what is here, or - while nobody but the admin has an account - load all of it. */
function ImportCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['seed'], queryFn: () => api<SeedState>('/api/system/seed'), refetchInterval: (query) => (query.state.data?.merge.state === 'running' ? 1000 : 15000) });
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ['seed'] });

  function upload(file: File) {
    setError(null);
    setProgress(0);
    const xhr = new XMLHttpRequest();                                   // fetch cannot report upload progress
    xhr.open('PUT', '/api/system/seed');
    xhr.setRequestHeader('X-Requested-With', 'qxs');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(e.loaded / e.total); };
    xhr.onload = () => {
      setProgress(null);
      if (xhr.status >= 200 && xhr.status < 300) refresh();
      else { try { setError(JSON.parse(xhr.responseText).detail ?? xhr.statusText); } catch { setError(xhr.statusText || 'upload failed'); } }
      if (input.current) input.current.value = '';
    };
    xhr.onerror = () => { setProgress(null); setError('the upload was interrupted'); };
    xhr.send(file);
  }
  const merge = useMutation({ mutationFn: () => api('/api/system/seed/merge', { method: 'POST', body: {} }), onSuccess: refresh, onError: (e: Error) => setError(e.message) });
  const discard = useMutation({ mutationFn: () => api('/api/system/seed', { method: 'DELETE' }), onSuccess: refresh });
  const replace = useMutation({
    mutationFn: () => api('/api/system/seed/replace', { method: 'POST', body: {} }),
    onSuccess: () => {
      setRestarting(true);                                              // the server restarts to load it: wait until it answers again, then reload the page
      const wait = setInterval(() => { fetch('/healthz').then((r) => { if (r.ok) { clearInterval(wait); window.location.reload(); } }).catch(() => undefined); }, 3000);
    },
    onError: (e: Error) => setError(e.message),
  });

  const d = q.data;
  const m = d?.merge;
  const p = d?.pending;
  return (
    <Card title="Import data from another installation" className="mt-4">
      <p className="note mb-3">
        Make the file on the computer that collected the data (<code>python -m app.seed make</code> in the backend folder), then upload it here. You can <b className="text-fg">add its candles</b> to this server at any
        time (what the server already has is kept; accounts, strategies and signals are never touched), or <b className="text-fg">load everything</b> (candles, signal history, strategies, trades) while nobody but you has an account.
      </p>
      {restarting && <div role="status" className="mb-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">Loading the data and restarting the server… this page reloads when it is back (about a minute).</div>}
      {error && <div role="alert" className="mb-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{error}</div>}
      <div className="flex flex-wrap items-center gap-3">
        <input ref={input} type="file" accept=".gz,.tgz,.db" className="text-sm" disabled={progress !== null || restarting} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} aria-label="Seed file" />
        {progress !== null && <div className="flex items-center gap-2 text-sm text-mut" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-2 w-40 overflow-hidden rounded-full bg-panel2"><div className="h-full bg-acc" style={{ width: `${progress * 100}%` }} /></div>{Math.round(progress * 100)}%</div>}
      </div>

      {d && p && !p.error && (
        <div className="mt-4 rounded-lg border border-line bg-panel2 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold">Ready to import <span className="font-normal text-mut">· {bytes(p.bytes)}{p.created ? ` · made ${ago(p.created, Date.now() / 1000)}` : ''}{p.files ? ` · ${p.files} AI / job files` : ''}</span></div>
            <button className="btn btn-sm" onClick={() => discard.mutate()} disabled={discard.isPending || m?.state === 'running'}>Discard</button>
          </div>
          <table className="w-full text-sm">
            <thead><tr><th className="th">What</th><th className="th text-right">In the file</th><th className="th text-right">On this server now</th></tr></thead>
            <tbody>{Object.keys(LABELS).filter((k) => k in p.counts).map((k) => (
              <tr key={k}><td className="td">{LABELS[k]}</td><td className="td text-right tabular-nums">{p.counts[k].toLocaleString()}</td><td className="td text-right tabular-nums text-mut">{(d.server[k] ?? '–').toLocaleString?.() ?? '–'}</td></tr>))}</tbody>
          </table>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button className="btn btn-primary" disabled={merge.isPending || m?.state === 'running' || restarting} onClick={() => merge.mutate()}>
              {m?.state === 'running' ? `Adding… ${m.done} / ${m.total} pairs` : 'Add the candles to this server'}</button>
            <button className="btn btn-danger" disabled={!d.fresh || replace.isPending || m?.state === 'running' || restarting}
              title={d.fresh ? 'Replaces this server\'s data with the file (the old database is kept next to it)' : 'Other users have accounts here: replacing would delete them'}
              onClick={() => confirm('Load EVERYTHING from the file and restart the server?\n\nThis server\'s own data (candles, signals, strategies) is replaced by the file\'s. The old database is kept on the disk next to it. You stay the admin from the settings file.') && replace.mutate()}>
              Load everything and restart</button>
            {!d.fresh && <span className="note">Loading everything is only possible while no one else has an account.</span>}
          </div>
          {m?.state === 'done' && m.result && <div role="status" className="mt-3 rounded-lg border border-up/40 bg-up/10 px-3 py-2 text-sm text-up">Added {m.result.candles.toLocaleString()} candles, {m.result.sentiment.toLocaleString()} sentiment rows and {m.result.instruments.toLocaleString()} new pairs. Nothing else was changed.</div>}
          {m?.state === 'error' && <div role="alert" className="mt-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">The merge failed: {m.error}</div>}
        </div>
      )}
      {p?.error && <div role="alert" className="mt-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">The waiting file is not usable: {p.error}</div>}
    </Card>
  );
}

export default function System() {
  const qc = useQueryClient();
  const sys = useQuery({ queryKey: ['system'], queryFn: () => api<Sys>('/api/system'), refetchInterval: 10000 });
  const live = useLiveState();
  const clear = useMutation({ mutationFn: () => api('/api/clear', { method: 'POST', body: {} }), onSuccess: () => qc.invalidateQueries() });
  if (sys.isError) return <ErrorBox error={sys.error} />;
  if (!sys.data) return <Spinner />;
  const s = sys.data;
  const errs = Object.entries(s.strategy_errors);

  return (
    <>
      <PageTitle title="System" sub="Backend health, storage and maintenance." />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Uptime" value={s.started ? ago(s.started, s.now).replace(' ago', '') : '–'} sub={`Python ${s.python}`} />
        <Stat label="Database" value={bytes(s.db_bytes + s.wal_bytes)} sub={`schema v${s.schema_version}`} />
        <Stat label="Strategies running" value={s.strategies_running} tone={errs.length ? 'down' : undefined} sub={errs.length ? `${errs.length} with errors` : 'no errors'} />
        <Stat label="Ticks received" value={s.ingest.ticks.toLocaleString()} sub={`${s.ingest.batches.toLocaleString()} batches`} />
        <Stat label="Last data" value={s.ingest.last_recv ? ago(s.ingest.last_recv, s.now) : 'never'} />
      </div>

      {errs.length > 0 && <Card title="Strategy errors" className="mt-4">{errs.map(([k, e]) => <div key={k} className="mb-2"><Badge tone="down">{k}</Badge> <span className="text-sm text-down">{e.join('; ')}</span></div>)}</Card>}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Stored rows">
          <table className="w-full"><tbody>{Object.entries(s.counts).map(([k, v]) => <tr key={k}><td className="td font-mono text-xs">{k}</td><td className="td text-right tabular-nums">{v.toLocaleString()}</td></tr>)}</tbody></table>
          <div className="note mt-2 break-all">{s.db_path}</div>
        </Card>
        <Card title="Feed log (latest events)">
          {!live.data ? <Spinner /> : (
            <div className="space-y-1 font-mono text-xs">{live.data.feed_events.slice(0, 18).map((e, i) => (
              <div key={i}><span className="text-mut">{ago(e.t, live.data!.now).padEnd(9)}</span> {e.e} <span className="text-mut">{e.d}</span></div>))}</div>
          )}
        </Card>
      </div>

      <Card title="Maintenance" className="mt-4">
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-danger" disabled={clear.isPending} onClick={() => confirm('Delete ALL recorded signals, features, outcomes and notes? Candles and strategies are kept. This cannot be undone.') && clear.mutate()}>Clear all signals</button>
          <span className="note">Keeps candles, instruments, strategies, backtests and insights. Backups are on the card below.</span>
        </div>
        {clear.isError && <div className="mt-2"><ErrorBox error={clear.error} /></div>}
      </Card>

      <BackupsCard />
      <ImportCard />
    </>
  );
}
