import { ReactNode, useEffect, useRef, useState } from 'react';

export interface UndoItem { key: string; title: string; detail?: ReactNode; onUndo: () => void }

const SECONDS = 10;

function Toast({ item, onDismiss }: { item: UndoItem; onDismiss: (key: string) => void }) {
  const [left, setLeft] = useState(SECONDS * 1000);
  const paused = useRef(false);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (paused.current) return;
      setLeft((l) => Math.max(0, l - 100));
    }, 100);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => { if (left <= 0) onDismiss(item.key); }, [left, item.key, onDismiss]);

  return (
    <div className="toast-in pointer-events-auto w-[min(92vw,420px)] overflow-hidden rounded-xl border border-line bg-panel shadow-2xl shadow-black/50"
      onMouseEnter={() => { paused.current = true; }} onMouseLeave={() => { paused.current = false; }}
      onFocus={() => { paused.current = true; }} onBlur={() => { paused.current = false; }}>
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-down/15 text-down" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" /></svg>
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">{item.title}</div>
          {item.detail && <div className="truncate text-xs text-mut">{item.detail}</div>}
        </div>
        <button type="button" className="btn btn-sm btn-primary shrink-0" onClick={() => { item.onUndo(); onDismiss(item.key); }}>
          Undo <span className="tabular-nums opacity-80">{Math.ceil(left / 1000)}s</span>
        </button>
        <button type="button" className="shrink-0 rounded-md p-1 text-mut hover:bg-panel2 hover:text-fg" aria-label="Dismiss" onClick={() => onDismiss(item.key)}>✕</button>
      </div>
      <div className="h-1 bg-panel2" aria-hidden="true"><div className="h-full bg-acc transition-[width] duration-100 ease-linear" style={{ width: `${(left / (SECONDS * 1000)) * 100}%` }} /></div>
    </div>
  );
}

/** Bottom-centre "snackbar" stack: each item shows for 10 seconds with an Undo button and a countdown bar (the countdown pauses while the pointer is over it). */
export default function UndoToasts({ items, onDismiss }: { items: UndoItem[]; onDismiss: (key: string) => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-5 z-50 flex flex-col items-center gap-2 px-3" role="status" aria-live="polite">
      {items.map((it) => <Toast key={it.key} item={it} onDismiss={onDismiss} />)}
    </div>
  );
}
