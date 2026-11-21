import { describe, expect, it } from 'vitest';
import { daysLeft, money, refProblem } from './pay';

const TRC = 'a'.repeat(64), BEP = '0x' + 'b'.repeat(64);

describe('refProblem', () => {
  it('accepts a 12-digit UPI reference, with spaces', () => {
    expect(refProblem('upi', null, '1234 5678 9012')).toBeNull();
    expect(refProblem('upi', null, '12345678901')).not.toBeNull();
    expect(refProblem('upi', null, '12345678901a')).not.toBeNull();
    expect(refProblem('upi', null, '')).toBe('Enter the payment reference.');
  });
  it('checks USDT hashes per network', () => {
    expect(refProblem('usdt', 'TRC20', TRC)).toBeNull();
    expect(refProblem('usdt', 'TRC20', BEP)).not.toBeNull();
    expect(refProblem('usdt', 'BEP20', BEP)).toBeNull();
    expect(refProblem('usdt', 'ERC20', TRC)).not.toBeNull();
    expect(refProblem('usdt', 'other', 'tx-1234567890abcdef')).toBeNull();
  });
  it('has a general rule for other methods', () => {
    expect(refProblem('other', null, 'ABC-123')).toBeNull();
    expect(refProblem('other', null, 'ab')).not.toBeNull();
  });
});

describe('formatting', () => {
  it('writes prices with their currency', () => {
    expect(money(499, 'INR')).toBe('₹499');
    expect(money(5, 'USDT')).toBe('$5 USDT');
    expect(money(4.5, 'USD')).toBe('$4.50');
    expect(money(10, 'JPY')).toBe('10 JPY');
  });
  it('counts whole days left, never below zero', () => {
    expect(daysLeft(1000 + 86400 * 2 + 5, 1000)).toBe(3);
    expect(daysLeft(500, 1000)).toBe(0);
    expect(daysLeft(null)).toBeNull();
  });
});
