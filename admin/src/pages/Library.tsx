import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, Strategy } from '../api';
import { ago, pct } from '../format';
import { Badge, Card, Empty, ErrorBox, PageTitle, Spinner, VerdictBadge } from '../components/ui';

interface Item extends Strategy { receiving: boolean; always: boolean }

/** The strategies the team has published: their measured record, never their logic. Adding one means its signals reach this account (cards and sounds in the extension). */
export default function Library() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['library'], queryFn: () => api<Item[]>('/api/library'), refetchInterval: 10000 });
  const toggle = useMutation({
    mutationFn: (s: Item) => (s.receiving ? api(`/api/library/${s.id}/subscribe`, { method: 'DELETE' }) : api(`/api/library/${s.id}/subscribe`, { method: 'POST', body: {} })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['library'] }),
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <Spinner />;
  return (
    <div>
      <PageTitle title="Strategy library" sub="Strategies the team has tested and published. Each one shows its real record so far. Add one to receive its signals; remove it any time." />
      {toggle.isError && <div role="alert" className="mb-3 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{(toggle.error as Error).message}</div>}
      <Card>
        {q.data.length === 0 ? <Empty>Nothing is published yet. When the team publishes a strategy it appears here.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>{['Strategy', 'Status', 'Signals', 'Won / lost', 'Win rate (95% range)', 'Verdict', 'Last signal', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{q.data.map((s) => (
                <tr key={s.id}>
                  <td className="td"><div className="font-semibold">{s.name}</div>{s.description && <div className="note max-w-md">{s.description}</div>}
                    <div className="note">{s.always ? 'sent to everybody' : 'add it to receive it'}</div></td>
                  <td className="td"><Badge tone={s.status === 'live' ? 'up' : s.status === 'paused' ? 'warn' : 'mut'}>{s.status.toUpperCase()}</Badge>
                    {s.status !== 'live' && <div className="note">{s.status === 'paper' ? 'no alerts yet' : 'not alerting'}</div>}</td>
                  <td className="td tabular-nums">{s.stats.total}</td>
                  <td className="td tabular-nums">{s.stats.wins} / {s.stats.losses}</td>
                  <td className="td tabular-nums">{pct(s.stats.accuracy)}{s.stats.settled > 0 && <span className="note"> ({pct(s.stats.ci_low, 0)}–{pct(s.stats.ci_high, 0)})</span>}
                    {s.stats.breakeven !== null && <div className="note">break-even {pct(s.stats.breakeven, 0)}</div>}</td>
                  <td className="td"><VerdictBadge settled={s.stats.settled} lo={s.stats.ci_low} hi={s.stats.ci_high} be={s.stats.breakeven} /></td>
                  <td className="td note">{ago(s.last_signal)}</td>
                  <td className="td text-right">
                    {s.receiving ? <button className="btn btn-sm" disabled={toggle.isPending} onClick={() => toggle.mutate(s)}>{s.always ? 'Switch off for me' : 'Remove'}</button>
                      : <button className="btn btn-sm btn-primary" disabled={toggle.isPending} onClick={() => toggle.mutate(s)}>{s.always ? 'Switch on' : 'Add to my account'}</button>}
                  </td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="note mt-3">A win rate is only meaningful with a few hundred settled signals; the verdict compares its 95% range with the break-even rate your payout implies. Past results do not promise future ones, and nothing here is financial advice.</p>
    </div>
  );
}
