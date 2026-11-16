import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, isPanel } from '../api';
import { SessionInfo, SessionSummary, SessionTally, useSessionStatus } from '../sessions';

/** The small "LIVE" tag with a pulsing dot, for the menu and the headers. */
export function LiveBadge({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border border-rose-500/50 bg-rose-500/15 px-2 py-0.5 text-[10px] font-extrabold tracking-widest text-rose-300 ${className}`}>
      <span className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-rose-500" />LIVE
    </span>
  );
}

type Dialog = { kind: 'start'; session: SessionInfo } | { kind: 'end'; session: SessionInfo; summary: SessionSummary | null };

/**
 * The dialogs that tell everybody a live session has started or ended (not the admin who pressed the button: they know). Driven by the light status poll, so they show on whatever
 * page the person is on; the extension shows its own version on the Quotex page.
 */
export function SessionDialogs({ enabled }: { enabled: boolean }) {
  const nav = useNavigate();
  const status = useSessionStatus(enabled && !isPanel());
  const prev = useRef<{ id: number; mine: boolean; session: SessionInfo } | null | undefined>(undefined);       // undefined = nothing read yet
  const [dlg, setDlg] = useState<Dialog | null>(null);
  const primary = useRef<HTMLButtonElement>(null);

  const d = status.data;
  useEffect(() => {
    if (!d) return;
    const seenKey = (id: number) => `qxs-sess-seen-${id}`;
    const was = prev.current;
    const now = d.live && d.session ? { id: d.session.id, mine: d.mine, session: d.session } : null;
    prev.current = now;
    if (now && !now.mine && (was === undefined || was === null || was.id !== now.id)) {
      let seen = false;
      try { seen = sessionStorage.getItem(seenKey(now.id)) === '1'; sessionStorage.setItem(seenKey(now.id), '1'); } catch { /* shown again after a reload: fine */ }
      if (!seen) setDlg({ kind: 'start', session: now.session });
    } else if (!now && was && !was.mine) {
      const ended = was.session;
      api<SessionSummary>(`/api/sessions/${was.id}`).then((s) => setDlg({ kind: 'end', session: ended, summary: s })).catch(() => setDlg({ kind: 'end', session: ended, summary: null }));
    }
  }, [d]);

  useEffect(() => {
    if (!dlg) return;
    primary.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDlg(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dlg]);

  if (!dlg) return null;
  const start = dlg.kind === 'start';
  const stats = dlg.kind === 'end' ? dlg.summary?.stats : null;
  const glow = start ? '244, 63, 94' : '139, 92, 246';
  const go = () => { setDlg(null); nav('/sessions' + (isPanel() ? window.location.search : '')); };
  return (
    <div className="dlg-back fixed inset-0 z-[300] grid place-items-center bg-black/60 p-4 backdrop-blur-sm" onMouseDown={(e) => { if (e.target === e.currentTarget) setDlg(null); }}>
      <div role="alertdialog" aria-modal="true" aria-label={start ? 'Live session started' : 'Live session ended'} className="dlg-card relative w-full max-w-md overflow-hidden rounded-2xl border bg-panel text-center shadow-2xl"
        style={{ borderColor: `rgba(${glow}, 0.55)`, boxShadow: `0 0 0 1px rgba(${glow}, 0.25), 0 24px 80px rgba(${glow}, 0.28), 0 0 60px rgba(${glow}, 0.18)` }}>
        <div className="h-1 w-full" style={{ background: `linear-gradient(90deg, transparent, rgb(${glow}), transparent)` }} />
        <div className="px-7 pb-6 pt-7">
          <div className="relative mx-auto mb-4 grid h-16 w-16 place-items-center">
            {start && <><span className="ring-out absolute inset-0 rounded-full border-2 border-rose-500/60" /><span className="ring-out absolute inset-0 rounded-full border-2 border-rose-500/40" style={{ animationDelay: '0.9s' }} /></>}
            <span className="relative grid h-14 w-14 place-items-center rounded-full text-2xl" style={{ background: `rgba(${glow}, 0.18)`, border: `1px solid rgba(${glow}, 0.6)` }}>{start ? '◉' : '■'}</span>
          </div>
          {start && <LiveBadge className="mb-2" />}
          <h2 className="text-xl font-extrabold tracking-tight">{start ? 'A live session has started' : 'The live session has ended'}</h2>
          <div className="mt-1.5 flex items-center justify-center gap-2 text-sm text-mut">
            <span className="font-semibold text-fg">{dlg.session.title}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${dlg.session.tier === 'premium' ? 'border-warn/50 bg-warn/10 text-warn' : 'border-line text-mut'}`}>{dlg.session.tier}</span>
          </div>
          <p className="mx-auto mt-3 max-w-xs text-sm text-mut">
            {start ? 'Signals will come with the candle and the area marked on the chart. Open the session to follow along, and keep an eye out for the heads-up before each signal.'
              : stats && stats.total > 0 ? 'Here is how it went. Every signal was counted, wins and losses.' : 'The host has wrapped up. The session record is in the history.'}
          </p>
          {!start && stats && stats.total > 0 && <div className="mt-4 flex justify-center rounded-xl border border-line bg-panel2 px-4 py-3"><SessionTally stats={stats} /></div>}
          <div className="mt-6 grid grid-cols-2 gap-2.5">
            <button className="btn justify-center" onClick={() => setDlg(null)}>{start ? 'Not now' : 'Close'}</button>
            <button ref={primary} className="btn btn-primary justify-center font-bold" onClick={go} style={start ? { boxShadow: '0 0 18px rgba(139,92,246,0.5)' } : undefined}>{start ? 'Join the session' : 'See the results'}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
