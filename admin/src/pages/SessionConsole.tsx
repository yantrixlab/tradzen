import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import type { LiveSelection } from '../hooks';
import { SignalSender } from '../components/SignalSender';
import { Badge, Card, ErrorBox, Spinner } from '../components/ui';
import { TrackRecord } from '../components/TrackRecord';
import { SessionHistory } from '../components/SessionHistory';
import { LiveView, PairPicker } from './LiveSignal';
import { SignalList, SessionTally, sessionOverlay, useLiveSession } from '../sessions';
import { prettyPair, tfName } from '../format';

function Elapsed({ since }: { since: number }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, []);
  const s = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return <span className="tabular-nums">{String(Math.floor(s / 3600)).padStart(2, '0')}:{String(Math.floor(s / 60) % 60).padStart(2, '0')}:{String(s % 60).padStart(2, '0')}</span>;
}

/** Start screen: a title and who it is for. */
function StartForm({ onStart, busy, error }: { onStart: (title: string, tier: 'free' | 'premium') => void; busy: boolean; error: unknown }) {
  const [title, setTitle] = useState('');
  const [tier, setTier] = useState<'free' | 'premium'>('free');
  return (
    <Card title="Start a live session">
      <p className="mb-3 text-sm text-mut">Everybody whose plan includes Live sessions gets the chart and your alerts while the session is live. Mark it premium to show its signals only to plans that include premium sessions.</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[240px] flex-1"><div className="note mb-1">Title</div><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} placeholder="Evening session" /></label>
        <label><div className="note mb-1">Who sees the signals</div>
          <select className="input" value={tier} onChange={(e) => setTier(e.target.value as 'free' | 'premium')}><option value="free">Free: everybody with Live sessions</option><option value="premium">Premium only</option></select></label>
        <button className="btn" disabled={busy} onClick={() => onStart(title, tier)}>▶ Start session</button>
      </div>
      {!!error && <div className="mt-3"><ErrorBox error={error} /></div>}
    </Card>
  );
}

export default function SessionConsole() {
  const qc = useQueryClient();
  const live = useLiveSession();
  const d = live.data;
  const s = d?.session ?? null;
  const refresh = () => { qc.invalidateQueries({ queryKey: ['live-session'] }); qc.invalidateQueries({ queryKey: ['session-history'] }); };
  const start = useMutation({ mutationFn: (v: { title: string; tier: string }) => api('/api/sessions', { body: v }), onSuccess: refresh });
  const end = useMutation({ mutationFn: () => api(`/api/sessions/${s!.id}/end`, { method: 'POST', body: {} }), onSuccess: refresh });
  const pair = useMutation({ mutationFn: (v: { symbol: string | null; tf?: number }) => api(`/api/sessions/${s!.id}/pair`, { method: 'PUT', body: v }), onSuccess: refresh });
  const act = useMutation({ mutationFn: (v: { id: number; what: 'send' | 'cancel' }) => api(`/api/sessions/${s!.id}/signals/${v.id}/${v.what}`, { method: 'POST', body: {} }), onSuccess: refresh });

  if (!d) return live.isError ? <ErrorBox error={live.error} /> : <Spinner />;
  return (
    <>
      <div className="mb-4"><h1 className="text-xl font-bold sm:text-2xl">Live session</h1>
        <p className="mt-1 text-sm text-mut">Run a session: send heads-ups and signals, and everybody watching sees the candle and the area on the chart, gets the alert, and sees the result.</p></div>
      {!s ? <StartForm onStart={(title, tier) => start.mutate({ title, tier })} busy={start.isPending} error={start.error} /> : (
        <>
          <Card className="mb-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3"><span className="inline-flex items-center gap-1.5 font-bold text-up"><span className="h-2 w-2 animate-pulse rounded-full bg-up" />LIVE</span>
                <span className="text-lg font-bold">{s.title}</span>{s.tier === 'premium' ? <Badge tone="warn">premium</Badge> : <Badge>free</Badge>}<Elapsed since={s.started} /></div>
              <div className="flex flex-wrap items-center gap-4"><SessionTally stats={d.stats} />
                <button className="btn btn-sm !border-down/60 !text-down" disabled={end.isPending} onClick={() => { if (window.confirm('End the session? Heads-ups that were not sent are dropped; signals already running still settle.')) end.mutate(); }}>■ End session</button></div>
            </div>
            {d.stats && d.stats.settled > 0 && <div className="mt-2"><TrackRecord stats={d.stats} note={false} /></div>}
          </Card>
          <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
            <div className="contents xl:block xl:space-y-4">
              <div className="order-1 xl:order-none"><SignalSender any showList={false} /></div>
              <div className="order-3 xl:order-none"><PairPicker data={{ symbols: d.symbols ?? [] } as unknown as LiveSelection} sym={d.pair?.symbol ?? ''} following={!!s.follow_admin}
                onPick={(sym) => pair.mutate(sym ? { symbol: sym, tf: d.pair?.tf } : { symbol: null })} /></div>
            </div>
            <div className="order-2 min-w-0 space-y-4 xl:order-none">
              {d.selected
                ? <LiveView sel={d.selected} tf={d.selected.tf} setTf={(t) => pair.mutate({ symbol: d.selected!.symbol, tf: t })} overlay={sessionOverlay(d.signals, d.selected.symbol, d.selected.tf)} count={40} />
                : <Card><div className="note p-6 text-center">No pair yet. Open a chart on Quotex (this chart follows it) or pick a pair from the list.</div></Card>}
              {d.pair?.symbol && <div className="note">Viewers see {prettyPair(d.pair.symbol)} on the {tfName(d.pair.tf)} chart{s.follow_admin ? ', following your Quotex tab' : ', the pair you picked'}.</div>}
              <Card title="Signals of this session">
                <SignalList signals={d.signals ?? []} actions={(sg) => (
                  <>{sg.status === 'headsup' && <button className="btn btn-sm mr-1" disabled={act.isPending} onClick={() => act.mutate({ id: sg.id, what: 'send' })}>Send</button>}
                    {(sg.status === 'headsup' || (sg.status === 'pending' && Date.now() / 1000 < sg.entry_t)) && <button className="btn btn-sm" disabled={act.isPending} onClick={() => act.mutate({ id: sg.id, what: 'cancel' })}>Cancel</button>}</>)} />
                {act.isError && <div className="mt-2"><ErrorBox error={act.error} /></div>}
              </Card>
            </div>
          </div>
        </>)}
      <div className="mt-4"><SessionHistory title="Past sessions" /></div>
    </>
  );
}
