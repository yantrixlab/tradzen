import { describe, expect, it } from 'vitest';
import { elapsedText, headline, Job, looksStuck, percent, withoutJob } from './jobs';

const job = (over: Partial<Job> = {}): Job => ({ key: 'research:1', kind: 'research', ref: '1', title: 'Research check', page: '/research', started: 1000, progress: 0.4, detail: '', can_stop: true, ...over });

describe('percent', () => {
  it('rounds to whole percent and clamps', () => {
    expect(percent(0.4)).toBe(40);
    expect(percent(0.456)).toBe(46);
    expect(percent(-1)).toBe(0);
    expect(percent(7)).toBe(100);
  });
  it('is null when the job reports no progress', () => {
    expect(percent(null)).toBeNull();
    expect(percent(undefined)).toBeNull();
    expect(percent(Number.NaN)).toBeNull();
  });
});

describe('elapsedText', () => {
  it('reads like a person would say it', () => {
    expect(elapsedText(1000, 1002)).toBe('just started');
    expect(elapsedText(1000, 1042)).toBe('42 s');
    expect(elapsedText(1000, 1000 + 185)).toBe('3 min 05 s');
    expect(elapsedText(1000, 1000 + 2 * 3600 + 10 * 60 + 30)).toBe('2 h 10 min');
  });
  it('never goes negative when the clocks differ a little', () => {
    expect(elapsedText(1010, 1000)).toBe('just started');
  });
  it('is empty without a start time', () => {
    expect(elapsedText(null, 1000)).toBe('');
  });
});

describe('headline', () => {
  it('counts', () => {
    expect(headline(0)).toBe('Nothing running');
    expect(headline(3)).toBe('3 running');
  });
});

describe('looksStuck', () => {
  it('flags a job far past a normal run, never the data refresh', () => {
    expect(looksStuck(job(), 1000 + 91 * 60)).toBe(true);
    expect(looksStuck(job(), 1000 + 10 * 60)).toBe(false);
    expect(looksStuck(job({ kind: 'marketdata' }), 1000 + 500 * 60)).toBe(false);
    expect(looksStuck(job({ started: null }), 1e9)).toBe(false);
  });
});

describe('withoutJob', () => {
  it('drops only the stopped job', () => {
    const list = [job(), job({ key: 'ai:train', kind: 'ai' })];
    expect(withoutJob(list, 'research:1').map((j) => j.key)).toEqual(['ai:train']);
  });
});
