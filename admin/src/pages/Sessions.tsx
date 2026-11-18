import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { Badge, Card, ErrorBox, Spinner } from '../components/ui';
import { TrackRecord } from '../components/TrackRecord';
import { SessionHistory } from '../components/SessionHistory';
import { LiveView } from './LiveSignal';
import MyChart from './MyChart';
import { clock, SessionSignal, SignalList, SessionTally, sessionOverlay, STATUS_WORD, useLiveSession } from '../sessions';
import { prettyPair, tfName } from '../format';

// ------------------------------------------------------------------ the page's own ping (for people without the extension): a short tone and a banner when a signal changes
let audio: AudioContext | null = null;
function tone(seq: [number, number][]) {
  try {
    audio = audio ?? new AudioContext();
    let t = audio.currentTime;
    for (const [hz, len] of seq) {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = hz; o.type = 'sine'; g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(g).connect(audio.destination); o.start(t); o.stop(t + len + 0.02); t += len + 0.05;
    }
  } catch { /* no audio available */ }
}
const SOUNDS: Record<string, [number, number][]> = {
  headsup: [[660, 0.15]], CALL: [[784, 0.14], [1046, 0.2]], PUT: [[784, 0.14], [587, 0.2]], win: [[660, 0.12], [880, 0.12], [1175, 0.25]], loss: [[440, 0.2], [330, 0.3]],
};

function usePing(signals: SessionSignal[] | undefined, soundOn: boolean) {
  const seen = useRef(new Map<number, string>());
  const first = useRef(true);
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!signals) return;
    let msg: string | null = null, snd: string | null = null;
    for (const s of signals) {
      const prev = seen.current.get(s.id);
      if (prev === s.status) continue;
      seen.current.set(s.id, s.status);
      if (first.current || s.status === 'void') continue;
      const pair = prettyPair(s.symbol);
      if (s.status === 'headsup') { msg = `Get ready: ${s.direction} on ${pair}, candle ${clock(s.entry_t)}`; snd = 'headsup'; }
      else if (s.status === 'pending') { msg = `${s.direction} ${pair}: take it on the ${clock(s.entry_t)} candle`; snd = s.direction; }
      else if (s.status === 'cancelled') { msg = `Cancelled: do not take ${s.direction} on ${pair}`; snd = 'loss'; }
      else { msg = `${pair} ${s.direction}: ${STATUS_WORD[s.status]}`; snd = s.status === 'win' ? 'win' : s.status === 'loss' ? 'loss' : null; }
    }
    first.current = false;
    if (msg) {
      setToast(msg);
      if (soundOn && snd) tone(SOUNDS[snd]);
      const t = setTimeout(() => setToast(null), 9000);
      return () => clearTimeout(t);
    }
  }, [signals, soundOn]);
  return toast;
}

/** What users see: the live session's chart with every signal marked (candle, area, direction, result), the signal feed and the session's record. */
export default function Sessions() {
  const qc = useQueryClient();
  const compact = new URLSearchParams(location.search).has('embed');
  const live = useLiveSession();
  const d = live.data;
  const [own, setOwn] = useState(false);
  const [soundOn, setSoundOn] = useState(() => { try { return localStorage.getItem('qxs-sess-sound') === '1'; } catch { return false; } });
  const toast = usePing(d?.signals, soundOn);
  const alerts = useMutation({ mutationFn: (on: boolean) => api('/api/sessions/alerts', { method: 'PUT', body: { on } }), onSuccess: () => qc.invalidateQueries({ queryKey: ['live-session'] }) });
  const toggleSound = () => { const v = !soundOn; setSoundOn(v); try { localStorage.setItem('qxs-sess-sound', v ? '1' : '0'); } catch { /* ignore */ } if (v) tone([[880, 0.12]]); };

  if (!d) return live.isError ? <ErrorBox error={live.error} /> : <Spinner />;
  const s = d.session;
  return (
    <>
      {!compact && <div className="mb-4"><h1 className="text-xl font-bold sm:text-2xl">Live sessions</h1>
        <p className="mt-1 text-sm text-mut">When a session is live you see its chart, the signals with the candle and area marked, and the results. Nothing here is financial advice, and every result is counted, wins and losses.</p></div>}
      {toast && <div className="mb-3 rounded-lg border border-acc/60 bg-acc/15 px-4 py-2 text-sm font-semibold text-[#c4b5fd]" role="status">🔔 {toast}</div>}
      {!s ? (
        <>
          <Card className="mb-4"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-mut">No session is live right now. Below is your own chart, following the pair open on your Quotex tab.</span><Badge>offline</Badge></div></Card>
          <MyChart />
        </>
      ) : (
        <>
          <Card className="mb-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3"><span className="inline-flex items-center gap-1.5 font-bold text-up"><span className="h-2 w-2 animate-pulse rounded-full bg-up" />LIVE</span>
                <span className="text-lg font-bold">{s.title}</span>{s.tier === 'premium' ? <Badge tone="warn">premium</Badge> : <Badge>free</Badge>}</div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <button className={`btn btn-sm ${soundOn ? '' : 'opacity-70'}`} onClick={toggleSound} title="A short tone in this page when a signal arrives">{soundOn ? '🔊 Sound on' : '🔈 Sound off'}</button>
                <button className={`btn btn-sm ${d.alerts_on === false ? 'opacity-70' : ''}`} disabled={alerts.isPending} onClick={() => alerts.mutate(d.alerts_on === false)} title="Alert cards and sounds in the Chrome extension">{d.alerts_on === false ? '🔕 Extension alerts off' : '🔔 Extension alerts on'}</button>
                <button className={`btn btn-sm ${own ? '' : 'opacity-70'}`} onClick={() => setOwn(!own)}>{own ? 'Show the session chart' : 'My own chart instead'}</button>
              </div>
            </div>
          </Card>
          {own ? <MyChart /> : (
            <div className="space-y-4">
              {d.locked && <Card><div className="p-3 text-center"><div className="text-lg font-bold">🔒 Premium session</div><p className="mt-1 text-sm text-mut">The signals and alerts of this session are part of the premium plan. You can still watch the chart.</p></div></Card>}
              {d.selected
                ? <LiveView sel={d.selected} tf={d.selected.tf} setTf={() => undefined} readOnly count={40} overlay={d.locked ? undefined : sessionOverlay(d.signals, d.selected.symbol, d.selected.tf)} />
                : <Card><div className="note p-6 text-center">The host has not opened a pair yet. The chart appears as soon as they do.</div></Card>}
              {d.pair?.symbol && <div className="note">{prettyPair(d.pair.symbol)} · {tfName(d.pair.tf)} chart, set by the host. Arrows sit on the candle the trade comes after, the shaded candle is the trade candle, the box is the area it is taken from.</div>}
              {!d.locked && (
                <Card title="Signals of this session" right={<SessionTally stats={d.stats} />}>
                  {d.stats && d.stats.settled > 0 && <div className="mb-2"><TrackRecord stats={d.stats} note={false} /></div>}
                  <SignalList signals={d.signals ?? []} />
                </Card>)}
            </div>)}
        </>)}
      <div className="mt-4"><SessionHistory title="Session history" /></div>
    </>
  );
}
