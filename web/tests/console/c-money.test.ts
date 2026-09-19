import { describe, expect, it } from 'vitest';
import { perMillion, perTask } from '../../src/lib/console/logic/money';

/**
 * Money, and the three absences behind one dash (REVIEW.md finding 9).
 *
 * The failure this guards against is quiet: an old server that has no
 * `cost_per_task` field at all would, if `undefined` were read as `null`, put
 * "no price, so no cost" under a model that has a price. So the dash is tested
 * for each absence separately, and the title -- the only place the two are told
 * apart -- is part of the assertion.
 */

describe('perTask', () => {
  it('never prints a dash for a number', () => {
    for (const n of [0, 0.0001, 0.004, 0.005, 0.06, 0.21, 1, 12.345, 999.99]) {
      expect(perTask(n).text).not.toBe('—');
      expect(perTask(n).title).toBe('');
    }
  });

  it('rounds to the cent, and says "less than a cent" rather than "$0.00"', () => {
    expect(perTask(0.06).text).toBe('$0.06');
    expect(perTask(0.214).text).toBe('$0.21');
    expect(perTask(12.3456).text).toBe('$12.35');
    expect(perTask(0.004).text).toBe('<$0.01');
  });

  it('shows a free task as a price, not as a missing one', () => {
    expect(perTask(0).text).toBe('$0.00');
  });

  it('tells "the server says none" from "the server never said"', () => {
    expect(perTask(null)).toEqual({ text: '—', title: 'no price, so no cost' });
    expect(perTask(undefined)).toEqual({
      text: '—',
      title: 'server does not report task cost'
    });
  });

  it('does not pretend an unreadable figure is a price', () => {
    expect(perTask(Number.NaN).text).toBe('—');
    expect(perTask(Number.POSITIVE_INFINITY).text).toBe('—');
  });
});

describe('perMillion', () => {
  it('keeps the posted decimals of a cheap model', () => {
    // $0.075 posted per 1M tokens is a real price, and $0.08 is not it
    expect(perMillion(0.075).text).toBe('$0.075');
    expect(perMillion(0.0035).text).toBe('$0.0035');
    expect(perMillion(1.25).text).toBe('$1.25');
    expect(perMillion(10).text).toBe('$10.00');
    expect(perMillion(150).text).toBe('$150.00');
  });

  it('never prints a dash for a number, including a free one', () => {
    expect(perMillion(0)).toEqual({ text: '$0.00', title: '' });
  });

  it('distinguishes the two absences, as the task cost does', () => {
    expect(perMillion(null)).toEqual({ text: '—', title: 'no posted price' });
    expect(perMillion(undefined)).toEqual({
      text: '—',
      title: 'no posted price field from this server'
    });
  });
});
