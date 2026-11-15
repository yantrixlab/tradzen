import type { Stats } from '../api';
import { pct } from '../format';

/**
 * What a strategy has MEASURABLY done on its real (live and paper) settled signals: its accuracy with a 95 % range, against the break-even it needs.
 * This is the number to trust. A signal's "strength" is only how many of the strategy's rules agree - for a one-rule strategy it is always 100 - and is not a chance of winning.
 */
export function TrackRecord({ stats, note = true }: { stats: Stats | undefined; note?: boolean }) {
  if (!stats || stats.settled < 1) return <div className="note">No settled signals yet, so there is no track record to show.</div>;
  const { accuracy, ci_low, ci_high, breakeven } = stats;
  const above = ci_low != null && breakeven != null && ci_low > breakeven;
  const below = ci_high != null && breakeven != null && ci_high < breakeven;
  return (
    <div className="rounded-lg bg-panel2 p-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <b className="text-fg">Track record</b>
        <span><b className="tabular-nums">{pct(accuracy)}</b> <span className="text-up">{stats.wins}W</span> / <span className="text-down">{stats.losses}L</span></span>
        <span className="note">95 % range {pct(ci_low, 0)}–{pct(ci_high, 0)}</span>
        <span className="note">break-even {pct(breakeven)}</span>
        <b className={above ? 'text-up' : below ? 'text-down' : 'text-warn'}>{above ? 'above break-even' : below ? 'below break-even' : "can't be told from break-even yet"}</b>
      </div>
      {note && <div className="note mt-1">Strength is only how many of the strategy's rules agree (always 100 for a one-rule strategy). It is not a chance of winning - this record is.</div>}
    </div>
  );
}
