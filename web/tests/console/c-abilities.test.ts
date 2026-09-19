import { describe, expect, it } from 'vitest';
import { NEEDS } from '../../src/lib/api/client';
import {
  NO_SOURCE_SAYS,
  SOURCE_NOT_REPORTED,
  describable,
  supportCount,
  tone,
  who
} from '../../src/lib/console/logic/abilities';

/**
 * Yes, no, and nobody said (CONSOLE.md section 6.7, REVIEW.md finding 10).
 *
 * The count beside a need is a count of `true`s, and "nobody measured it" is
 * never one of them: a pool nobody answered about says so once, in words,
 * rather than showing a zero that reads like a verdict.
 */

describe('tone', () => {
  it('has three states, not two', () => {
    expect(tone(true)).toBe('yes');
    expect(tone(false)).toBe('no');
    expect(tone(null)).toBe('unknown');
    expect(tone(undefined)).toBe('unknown');
  });
});

describe('who', () => {
  it('names the sources behind a yes', () => {
    expect(who({ answer: true, yes: ['inventory: openrouter', 'manual'] })).toBe('openrouter, manual');
  });

  it('names the sources behind a no', () => {
    expect(who({ answer: false, no: ['inventory: models.dev'] })).toBe('models.dev');
  });

  it('says nobody answered when nobody answered', () => {
    expect(who({ answer: null })).toBe(NO_SOURCE_SAYS);
    expect(who(null)).toBe(NO_SOURCE_SAYS);
    expect(who(undefined)).toBe(NO_SOURCE_SAYS);
  });

  it('does not claim nobody answered when somebody did but is unnamed', () => {
    expect(who({ answer: true, yes: [] })).toBe(SOURCE_NOT_REPORTED);
    expect(who({ answer: false })).toBe(SOURCE_NOT_REPORTED);
  });
});

describe('supportCount', () => {
  const pool = [
    { abilities: { vision: true, tools: true, reasoning: null, structured_output: null } },
    { abilities: { vision: true, tools: false } },
    { abilities: { vision: null } },
    {}
  ];

  it('counts yeses and only yeses', () => {
    expect(supportCount(pool)).toEqual({ vision: 2, reasoning: 0, tools: 1, structured_output: 0 });
  });

  it('counts every need', () => {
    expect(Object.keys(supportCount(pool)).sort()).toEqual([...NEEDS].sort());
  });

  it('counts nothing when nobody said anything', () => {
    expect(supportCount(pool.slice(3))).toEqual({
      vision: 0,
      reasoning: 0,
      tools: 0,
      structured_output: 0
    });
  });
});

describe('describable', () => {
  it('is true as soon as one row has one answer', () => {
    expect(describable([{ abilities: { vision: null } }, { abilities: { vision: true } }])).toBe(true);
    expect(describable([{ abilities: { vision: false } }])).toBe(true);
  });

  it('is false when every answer is unknown, and when there are no rows', () => {
    expect(describable([{ abilities: { vision: null, tools: null } }, {}])).toBe(false);
    expect(describable([])).toBe(false);
  });
});
