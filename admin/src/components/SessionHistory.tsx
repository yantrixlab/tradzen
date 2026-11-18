import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { dateTime, pct } from '../format';
import { Badge, Card, Empty, Spinner } from './ui';
import { SessionSignal, SessionStats, SessionSummary, SignalList, SessionTally, useSessionHistory } from '../sessions';
import { TrackRecord } from './TrackRecord';
import { useLast } from '../lib/useLast';

interface DetailData extends Omit<SessionSummary, "signals"> { signals: SessionSignal[]; stats: SessionStats | null }

function Detail({ id }: { id: number }) {
  const q = useQuery({ queryKey: ['session-detail', id], queryFn: () => api<DetailData>(`/api/sessions/${id}`), refetchInterval: 15000 });
  if (!q.data) return <Spinner />;
  if (q.data.locked) return <div className="note p-3">This is a premium session. Its signals and results are part of the premium plan.</div>;
  return <div className="space-y-2 p-2"><SessionTally stats={q.data.stats} /><TrackRecord stats={q.data.stats ?? undefined} note={false} /><SignalList signals={q.data.signals} /></div>;
}

/** Past (and current) sessions with their record: one row each, click to open the signals. Premium sessions show a non-entitled user only that they exist. */
export function SessionHistory({ title = 'Sessions' }: { title?: string }) {
  const q = useSessionHistory();
  const [open, setOpen] = useState<number | null>(null);
  const last = useLast(open);
  return (
    <Card title={title}>
      {!q.data ? <Spinner /> : q.data.length === 0 ? <Empty>No sessions yet.</Empty> : (
        <div className="divide-y divide-line">
          {q.data.map((s) => (
            <div key={s.id} className={last === s.id ? 'rounded-lg bg-acc/10' : ''}>
              <button className="grid w-full grid-cols-[1fr_auto] items-center gap-3 px-1 py-2 text-left" onClick={() => setOpen(open === s.id ? null : s.id)} aria-expanded={open === s.id}>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><span className="truncate font-semibold">{s.title}</span>
                    {s.tier === 'premium' ? <Badge tone="warn">premium</Badge> : <Badge>free</Badge>}{s.status === 'live' && <Badge tone="up">live now</Badge>}</div>
                  <div className="note">{dateTime(s.started)}{s.ended ? ` – ${dateTime(s.ended)}` : ''} · {s.signals} signal{s.signals === 1 ? '' : 's'}</div>
                </div>
                <div className="text-right text-sm">{s.locked ? <span className="note">🔒 premium</span> : s.stats && s.stats.wins + s.stats.losses > 0
                  ? <><b className="tabular-nums">{pct(s.stats.accuracy, 0)}</b> <span className="note">{s.stats.wins}W / {s.stats.losses}L</span></> : <span className="note">no result yet</span>}</div>
              </button>
              {open === s.id && <Detail id={s.id} />}
            </div>))}
        </div>)}
    </Card>
  );
}
