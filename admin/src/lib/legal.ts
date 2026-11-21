/** Types and helpers for the legal texts (terms of use, risk warning, refund policy) and the "your plan is ending" reminder. */
export interface LegalSection { key: 'terms' | 'risk' | 'refund'; title: string; text: string }
export interface LegalDoc { version: number; sections: LegalSection[]; edited: boolean }

export const REMIND_DAYS = 3;

/** Whether to remind the person about their plan: 'ending' in the last REMIND_DAYS days, 'ended' once it has run out (they are back on the default plan), else null. */
export function planNotice(planId: number | null, until: number | null, now = Date.now() / 1000): { state: 'ending' | 'ended'; days: number } | null {
  if (planId === null || !until) return null;
  if (until <= now) return { state: 'ended', days: 0 };
  const days = Math.ceil((until - now) / 86400);
  return days <= REMIND_DAYS ? { state: 'ending', days } : null;
}
