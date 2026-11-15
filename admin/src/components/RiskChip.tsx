import type { Signal } from '../api';

/** "RISKY" marker for a signal whose context matches a loss pattern learned from its strategy's backtest trades (nothing is shown for ordinary signals). */
export function RiskChip({ risk }: { risk: Signal['risk'] }) {
  if (!risk || risk.tag !== 'risky') return null;
  return (
    <span className="ml-1.5 inline-flex items-center rounded border border-warn/50 bg-warn/15 px-1.5 text-[10px] font-bold text-warn" title={risk.reasons.join('\n')}>⚠ RISKY</span>
  );
}

export function RiskReasons({ risk }: { risk: Signal['risk'] }) {
  if (!risk || risk.tag !== 'risky') return null;
  return (
    <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">
      <div className="font-semibold text-warn">⚠ Risky situation</div>
      <ul className="mt-1 list-disc pl-5">{risk.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      <div className="note mt-1">Learned from this strategy's old backtest trades and confirmed on its newest ones. A warning, not a guarantee: most such trades still win or lose by chance.</div>
    </div>
  );
}
