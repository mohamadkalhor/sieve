/**
 * The browser's scoring must equal the server's, on the same fixture.
 *
 * `tests/fixtures/rank_case.json` at the repository root is the shared case;
 * `tests/test_scoring.py` asserts the Python side against the same file. If
 * these two ever disagree, the profile editor lies to the person using it:
 * the list they drag is not the list they would get.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  carriedBy,
  rankOrder,
  rankWithFloor,
  renormalise,
  weigh,
  type AxesByModel
} from '../src/lib/rank/weigh';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(resolve(here, '../../tests/fixtures/rank_case.json'), 'utf-8')
) as {
  profile: { weights: Record<string, number>; policy: { min_confidence: number } };
  models: { id: string; axes: Record<string, { value: number | null; coverage: number }> }[];
  expected: {
    order: string[];
    scores: Record<string, number>;
    confidence: Record<string, number>;
    contributions: Record<string, Record<string, number>>;
    excluded: Record<string, string>;
  };
};

const axesByModel: AxesByModel = Object.fromEntries(
  fixture.models.map((model) => [model.id, model.axes])
);
const weights = fixture.profile.weights;
const floor = fixture.profile.policy.min_confidence;

describe('weigh, against the shared fixture', () => {
  const scored = weigh(axesByModel, weights);

  it('reproduces every score and confidence to 1e-6', () => {
    expect(scored).toHaveLength(12);
    for (const row of scored) {
      expect(row.score).toBeCloseTo(fixture.expected.scores[row.model_id], 6);
      expect(row.confidence).toBeCloseTo(fixture.expected.confidence[row.model_id], 6);
    }
  });

  it('reproduces every per-axis contribution', () => {
    for (const row of scored) {
      for (const [axis, value] of Object.entries(row.contributions)) {
        expect(value).toBeCloseTo(fixture.expected.contributions[row.model_id][axis], 6);
      }
    }
  });

  it('produces the expected order once the confidence floor is applied', () => {
    const kept = scored.filter((row) => row.confidence >= floor);
    expect(rankOrder(kept).map((row) => row.model_id)).toEqual(fixture.expected.order);
  });

  it('excludes exactly the models the fixture excludes', () => {
    const excluded = scored.filter((row) => row.confidence < floor).map((row) => row.model_id);
    expect(excluded.sort()).toEqual(Object.keys(fixture.expected.excluded).sort());
  });

  it('scores an unmeasured axis as nothing and shows the loss in confidence', () => {
    const m11 = scored.find((row) => row.model_id === 'm11');
    expect(m11?.contributions.c).toBe(0);
    expect(m11?.confidence).toBeCloseTo(0.7, 9);
    expect(m11!.confidence).toBeLessThan(floor);
  });

  it('ignores axes the profile does not weight', () => {
    const withNoise: AxesByModel = Object.fromEntries(
      Object.entries(axesByModel).map(([id, axes]) => [
        id,
        { ...axes, d: { value: 0, coverage: 1 }, e: { value: 0, coverage: 1 } }
      ])
    );
    expect(weigh(withNoise, weights)).toEqual(scored);
  });

  it('breaks an exact tie by model id', () => {
    expect(fixture.expected.scores.m03).toBe(fixture.expected.scores.m05);
    const order = fixture.expected.order;
    expect(order.indexOf('m03')).toBeLessThan(order.indexOf('m05'));
  });

  it('puts models under the floor last rather than dropping them', () => {
    const ranked = rankWithFloor(scored, floor);
    expect(ranked).toHaveLength(12);
    expect(ranked[ranked.length - 1].model_id).toBe('m11');
  });
});

describe('renormalise', () => {
  const weights3 = { a: 0.4, b: 0.3, c: 0.3 };

  it('keeps the sum at 1 wherever the slider goes', () => {
    for (const value of [0, 0.1, 0.25, 0.5, 0.75, 0.99, 1]) {
      const moved = renormalise(weights3, 'a', value);
      const total = Object.values(moved).reduce((sum, w) => sum + w, 0);
      expect(total).toBeCloseTo(1, 12);
      expect(moved.a).toBeCloseTo(value, 12);
    }
  });

  it('shares the remainder in proportion, so the others keep their order', () => {
    const moved = renormalise({ a: 0.5, b: 0.3, c: 0.2 }, 'a', 0.0);
    expect(moved.b / moved.c).toBeCloseTo(0.3 / 0.2, 9);
  });

  it('leaves a locked axis alone', () => {
    const moved = renormalise(weights3, 'a', 0.6, new Set(['b']));
    expect(moved.b).toBeCloseTo(0.3, 12);
    expect(Object.values(moved).reduce((s, w) => s + w, 0)).toBeCloseTo(1, 12);
  });

  it('clamps a slider that is dragged past its ends', () => {
    expect(renormalise(weights3, 'a', 2).a).toBe(1);
    expect(renormalise(weights3, 'a', -1).a).toBe(0);
  });
});

describe('carriedBy', () => {
  it('names the axis holding most of the gap', () => {
    const found = carriedBy({ a: 0.45, b: 0.1, c: 0.05 }, { a: 0.2, b: 0.09, c: 0.2 });
    expect(found?.axis).toBe('a');
    expect(found?.delta).toBeCloseTo(0.25, 9);
  });

  it('returns nothing when the leader is behind everywhere', () => {
    expect(carriedBy({ a: 0.1 }, { a: 0.5 })).toBeNull();
  });
});

describe('reduced motion', () => {
  it('collapses an animation to nothing when motion is not wanted', async () => {
    const { duration } = await import('../src/lib/motion/reduced');
    expect(duration(240, false)).toBe(240);
    expect(duration(240, true)).toBe(0);
  });
});
