import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import type { LiveSelection } from '../hooks';
import { Badge, Card, ErrorBox, Spinner } from '../components/ui';
import { TrackRecord } from '../components/TrackRecord';
import { SessionHistory } from '../components/SessionHistory';
import { LiveView, PairPicker } from './LiveSignal';
import { clock, SessionSignal, SignalList, SessionTally, sessionOverlay, useLiveSession } from '../sessions';
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

/** The signal form: direction, which candle, how long, the area it is taken from, a note, then heads-up or send. */
function SignalForm({ sid, sel, now, tfSec }: { sid: number; sel: LiveSelection['selected']; now: number; tfSec: number }) {
  const qc = useQueryClient();
  const [dir, setDir] = useState<'CALL' | 'PUT'>('CALL');
  const [entry, setEntry] = useState<'next' | 'after'>('next');
  const [expiry, setExpiry] = useState(1);
  const [lo, setLo] = useState('');
  const [hi, setHi] = useState('');
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const create = useMutation({
    mutationFn: (send: boolean) => api<SessionSignal>(`/api/sessions/${sid}/signals`, { body: { direction: dir, entry, expiry, area_lo: lo === '' ? null : Number(lo), area_hi: hi === '' ? null : Number(hi), area_label: label, note, send } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['live-session'] }); setNote(''); },
  });
  const cur = sel?.candles[sel.candles.length - 1];
  const fill = () => { if (cur) { setLo(String(cur.l)); setHi(String(cur.h)); } };
  const entryT = (Math.floor(now / tfSec) + (entry === 'next' ? 1 : 2)) * tfSec;
  const half = !!lo !== !!hi;
  const ok = !!sel && !half && !create.isPending;
  return (
    <Card title="Send a signal">
      <div className="mb-3 grid grid-cols-2 gap-2">
        {(['CALL', 'PUT'] as const).map((d) => (
          <button key={d} onClick={() => setDir(d)} className={`rounded-lg border px-3 py-2 text-base font-extrabold ${dir === d ? (d === 'CALL' ? 'border-up bg-up/20 text-up' : 'border-down bg-down/20 text-down') : 'border-line bg-panel2 text-mut hover:text-fg'}`}>{d === 'CALL' ? '▲ CALL' : '▼ PUT'}</button>))}
      </div>
      <div className="mb-3 grid grid-cols-2 gap-2 text-sm">
        <label><div className="note mb-1">Trade candle</div><select className="input" value={entry} onChange={(e) => setEntry(e.target.value as 'next' | 'after')}><option value="next">Next candle</option><option value="after">The one after</option></select></label>
        <label><div className="note mb-1">Candles it runs</div><select className="input" value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
      </div>
      <div className="note mb-2">Entry candle opens at <b className="text-fg">{clock(entryT)}</b> ({tfName(tfSec)} chart).</div>
      <div className="mb-1 flex items-end gap-2">
        <label className="flex-1"><div className="note mb-1">Area from</div><input className="input" inputMode="decimal" value={lo} onChange={(e) => setLo(e.target.value)} placeholder="price" /></label>
        <label className="flex-1"><div className="note mb-1">to</div><input className="input" inputMode="decimal" value={hi} onChange={(e) => setHi(e.target.value)} placeholder="price" /></label>
        <button className="btn btn-sm" type="button" onClick={fill} disabled={!cur} title="Fill the area with the high and low of the newest candle">Last candle</button>
      </div>
      <div className="mb-2 grid grid-cols-2 gap-2"><input className="input" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} placeholder="Area name (support, demand ...)" />
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Note for everybody" /></div>
      {half && <div className="mb-2 text-xs text-warn">Give both ends of the area, or leave both empty.</div>}
      <div className="grid grid-cols-2 gap-2">
        <button className="btn btn-sm" disabled={!ok} onClick={() => create.mutate(false)} title="Tell everybody to get ready: the candle and area are marked on their chart, nothing to trade yet">Send heads-up</button>
        <button className="btn" disabled={!ok} onClick={() => create.mutate(true)} title="Tell everybody to take this trade on the entry candle">Send signal now</button>
      </div>
      {!sel && <div className="mt-2 text-xs text-warn">No pair yet: open a chart on Quotex or pick a pair.</div>}
      {create.isError && <div className="mt-2"><ErrorBox error={create.error} /></div>}
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
      <div className="mb-4"><h1 className="text-2xl font-bold">Live session</h1>
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
          <div className="grid gap-4 xl:grid-cols-[340px_1fr]">
            <div className="space-y-4">
              <SignalForm sid={s.id} sel={d.selected ?? null} now={d.now} tfSec={d.pair?.tf ?? 60} />
              <PairPicker data={{ symbols: d.symbols ?? [] } as unknown as LiveSelection} sym={d.pair?.symbol ?? ''} following={!!s.follow_admin}
                onPick={(sym) => pair.mutate(sym ? { symbol: sym, tf: d.pair?.tf } : { symbol: null })} />
            </div>
            <div className="min-w-0 space-y-4">
              {d.selected
                ? <LiveView sel={d.selected} tf={d.selected.tf} setTf={(t) => pair.mutate({ symbol: d.selected!.symbol, tf: t })} overlay={sessionOverlay(d.signals, d.selected.symbol, d.selected.tf)} count={40} />
                : <Card><div className="note p-6 text-center">No pair yet. Open a chart on Quotex (this chart follows it) or pick a pair on the left.</div></Card>}
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
