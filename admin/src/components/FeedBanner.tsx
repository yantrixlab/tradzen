import type { FeedHealth } from '../hooks';

/** "Signals are paused" with the cause and what to do about it. Renders nothing while signals flow. */
export function FeedBanner({ health }: { health: FeedHealth | undefined }) {
  if (!health?.paused) return null;
  const hard = health.reasons.filter((r) => r.level !== 'info');
  const notes = health.reasons.filter((r) => r.level === 'info');
  const bad = hard.some((r) => r.level === 'bad');
  return (
    <div role="status" className={`mb-4 rounded-xl border p-3 ${bad ? 'border-down/50 bg-down/10' : 'border-warn/50 bg-warn/10'}`}>
      <div className={`flex items-center gap-2 text-sm font-bold ${bad ? 'text-down' : 'text-warn'}`}><span>{bad ? '⛔' : '⏸'}</span>Signals are paused</div>
      {hard.map((r) => (
        <div key={r.code} className="mt-2">
          <div className="text-sm font-semibold">{r.title}</div>
          <div className="mt-0.5 text-xs leading-relaxed text-mut">{r.detail}</div>
          {r.need ? <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-line"><div className="h-full bg-acc" style={{ width: `${Math.min(100, (100 * (r.have ?? 0)) / r.need)}%` }} /></div> : null}
          {r.fix && <div className="mt-1.5 rounded-lg bg-white/5 px-2.5 py-1.5 text-xs leading-relaxed"><b className="text-[#c4b5fd]">What to do:</b> {r.fix}</div>}
        </div>))}
      {notes.length > 0 && (
        <details className="mt-2 text-xs"><summary className="cursor-pointer text-mut">Also good to know ({notes.length})</summary>
          {notes.map((r) => <div key={r.code} className="mt-1.5"><b>{r.title}</b><div className="text-mut">{r.detail}</div></div>)}</details>)}
    </div>
  );
}
