import { describe, expect, it } from 'vitest';
import { planNotice } from './legal';

describe('planNotice', () => {
  const DAY = 86400, now = 1_000_000;
  it('says nothing for a free account, a plan with no end or a plan with plenty of time', () => {
    expect(planNotice(null, now + DAY, now)).toBeNull();
    expect(planNotice(2, null, now)).toBeNull();
    expect(planNotice(2, now + 10 * DAY, now)).toBeNull();
    expect(planNotice(2, now + 3 * DAY + 5, now)).toBeNull();
  });
  it('reminds in the last three days, counting whole days left', () => {
    expect(planNotice(2, now + 3 * DAY, now)).toEqual({ state: 'ending', days: 3 });
    expect(planNotice(2, now + DAY + 1, now)).toEqual({ state: 'ending', days: 2 });
    expect(planNotice(2, now + 60, now)).toEqual({ state: 'ending', days: 1 });
  });
  it('says the plan has ended once the time has run out', () => {
    expect(planNotice(2, now - 1, now)).toEqual({ state: 'ended', days: 0 });
    expect(planNotice(2, now, now)).toEqual({ state: 'ended', days: 0 });
  });
});
