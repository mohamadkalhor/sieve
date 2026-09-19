import { describe, expect, it } from 'vitest';
import type { Listed } from '../../src/lib/api/client';
import {
  multipliers,
  versusLeader,
  versusText,
  whyRows
} from '../../src/lib/console/logic/explain';

/**
 * The two numbers a row carries, kept apart (REVIEW.md finding 11).
 *
 * `raw` is what the axes gave a model; `score` is what it ended up with after
 * health and the router prefix trim. The explanation may only call an axis gap
 * an axis gap, and when the multipliers are what reordered a pair it has to say
 * so rather than blame an axis that did not do it.
 */

const WEIGHTS = { quality: 0.3, cost: 0.25, agentic: 0.2, latency: 0.15, context: 0.1 };
const LABELS = { quality: 'Quality', cost: 'Cost', agentic: 'Agentic coding' };

function row(over: Partial<Listed> & { id: string }): Listed {
  return {
    name: 'model',
    local_ids: [],
    score: 0.5,
    ...over
  };
}

function axes(entries: [string, number | null, number][]): Listed['axes'] {
  return entries.map(([axis, value, contribution]) => ({
    axis,
    value,
    coverage: value === null ? 0 : 1,
    contribution
  }));
}

describe('whyRows', () => {
  const scored = row({
    id: 'a',
    name: 'A',
    score: 0.37,
    raw: 0.37,
    axes: axes([
      ['quality', 0.9, 0.27],
      ['cost', 0.4, 0.1],
      ['agentic', null, 0]
    ])
  });

  it('accounts for exactly the raw score', () => {
    const rows = whyRows(scored, WEIGHTS, Object.keys(WEIGHTS), LABELS) ?? [];
    const total = rows.reduce((sum, one) => sum + one.got, 0);
    expect(Math.abs(total - (scored.raw ?? 0) * 100)).toBeLessThan(1e-6);
  });

  it('draws one row per proposed axis, in the order the weights name them', () => {
    const rows = whyRows(scored, WEIGHTS, Object.keys(WEIGHTS), LABELS) ?? [];
    expect(rows.map((one) => one.axis)).toEqual(Object.keys(WEIGHTS));
  });

  it('scales the bar against the biggest weight, not against the winner', () => {
    const rows = whyRows(scored, WEIGHTS, Object.keys(WEIGHTS), LABELS) ?? [];
    const quality = rows.find((one) => one.axis === 'quality');
    expect(quality?.got).toBeCloseTo(27, 9);
    expect(quality?.of).toBeCloseTo(30, 9);
    expect(quality?.share).toBeCloseTo(27 / 30, 9);
  });

  it('marks an unmeasured axis as unmeasured rather than as zero weight', () => {
    const rows = whyRows(scored, WEIGHTS, Object.keys(WEIGHTS), LABELS) ?? [];
    const agentic = rows.find((one) => one.axis === 'agentic');
    expect(agentic?.measured).toBe(false);
    expect(agentic?.got).toBe(0);
    expect(agentic?.of).toBeCloseTo(20, 9);
  });

  it('leaves nothing out when the weights name an axis nothing scored', () => {
    const rows = whyRows(scored, WEIGHTS, Object.keys(WEIGHTS), LABELS) ?? [];
    const latency = rows.find((one) => one.axis === 'latency');
    expect(latency?.measured).toBe(false);
    expect(latency?.got).toBe(0);
    expect(latency?.label).toBe('latency');
  });

  it('says nothing at all when the server does not say how the score was made', () => {
    expect(whyRows(row({ id: 'b' }), WEIGHTS, Object.keys(WEIGHTS), LABELS)).toBeNull();
  });

  it('does not divide by zero when every proposed weight is zero', () => {
    const rows = whyRows(scored, { quality: 0, cost: 0 }, ['quality', 'cost'], LABELS) ?? [];
    for (const one of rows) expect(Number.isFinite(one.share)).toBe(true);
    for (const one of rows) expect(one.share).toBe(0);
  });
});

describe('multipliers', () => {
  it('is silent when neither multiplier bites', () => {
    expect(multipliers(row({ id: 'a', health: 1, factor: 1 }))).toBeNull();
    expect(multipliers(row({ id: 'a' }))).toBeNull();
  });

  it('shows both when either one is not 1', () => {
    expect(multipliers(row({ id: 'a', health: 0.8 }))).toEqual({ health: 0.8, factor: 1 });
    expect(multipliers(row({ id: 'a', factor: 1.2 }))).toEqual({ health: 1, factor: 1.2 });
  });
});

describe('versusLeader', () => {
  const leader = row({
    id: 'opus',
    name: 'Opus',
    score: 0.85,
    raw: 0.85,
    axes: axes([
      ['quality', 0.9, 0.45],
      ['cost', 0.4, 0.25],
      ['agentic', 0.6, 0.15]
    ])
  });
  const follower = row({
    id: 'sonnet',
    name: 'Sonnet',
    score: 0.8,
    raw: 0.8,
    axes: axes([
      ['quality', 0.4, 0.2],
      ['cost', 0.7, 0.35],
      ['agentic', 0.8, 0.25]
    ])
  });

  it('names the axis carrying the gap and the axis the row wins', () => {
    const versus = versusLeader(follower, leader, LABELS);
    expect(versus?.behindBy).toBeCloseTo(5, 9);
    expect(versus?.mostlyOn).toBe('Quality');
    // cost and agentic are within an ulp of each other here, so the winner of
    // that tie is not this test's business: the widest one has its own test
    expect(versus?.aheadOn?.axis).not.toBe('Quality');
    expect(versus?.aheadOn?.by).toBeCloseTo(10, 9);
    expect(versus?.byMultipliers).toBe(false);
  });

  it('names the widest axis the row wins', () => {
    const wide = row({
      ...follower,
      axes: axes([
        ['quality', 0.4, 0.2],
        ['cost', 0.7, 0.35],
        ['agentic', 0.95, 0.3]
      ])
    });
    const versus = versusLeader(wide, leader, LABELS);
    expect(versus?.aheadOn?.axis).toBe('Agentic coding');
    expect(versus?.aheadOn?.by).toBeCloseTo(15, 9);
  });

  it('refuses to call a high-scoring row behind', () => {
    const ahead = row({ ...follower, score: 0.9, raw: 0.9 });
    const versus = versusLeader(ahead, leader, LABELS);
    expect(versus?.behindBy).toBeLessThan(0);
    expect(versusText(versus)).toContain('Ahead of Opus');
    expect(versusText(versus)).not.toContain('Behind');
  });

  it('blames the multipliers, not an axis, when they decided the order', () => {
    // the row loses the final score while winning on the axes
    const trimmed = row({ ...follower, score: 0.8, raw: 0.9 });
    const versus = versusLeader(trimmed, leader, LABELS);
    expect(versus?.behindBy).toBeCloseTo(5, 9);
    expect(versus?.rawBehindBy).toBeCloseTo(-5, 9);
    expect(versus?.byMultipliers).toBe(true);
    expect(versus?.mostlyOn).toBeNull();
    const said = versusText(versus);
    expect(said).toContain('health and trim');
    expect(said).not.toContain('mostly on');
  });

  it('says level with a row that ties it', () => {
    const level = row({ ...follower, score: 0.85, raw: 0.85 });
    const said = versusText(versusLeader(level, leader, LABELS));
    expect(said).toContain('Level with Opus');
    expect(said).not.toContain('Behind');
  });

  it('has nothing to say without a leader, or about itself, or without axes', () => {
    expect(versusLeader(follower, null, LABELS)).toBeNull();
    expect(versusLeader(follower, follower, LABELS)).toBeNull();
    expect(versusLeader(row({ id: 'x' }), leader, LABELS)).toBeNull();
    expect(versusLeader(follower, row({ id: 'y' }), LABELS)).toBeNull();
    expect(versusText(null)).toBe('');
  });

  it('falls back to the axis name when nobody labelled it', () => {
    const versus = versusLeader(follower, leader, {});
    expect(versus?.mostlyOn).toBe('quality');
  });
});
