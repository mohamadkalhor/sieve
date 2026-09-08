/**
 * How long one slider input costs, as pure arithmetic, on a 60-row list.
 *
 * Brief D says the editor must "re-rank under 16 ms per input on a 60-row
 * list" -- one frame at 60 fps. This file measures the computation half of
 * that: exactly the work `move()` and the `live` $derived do in
 * `routes/(app)/profiles/[name]/+page.svelte` for a single input event, which
 * is `renormalise` (the weight vector absorbs the drag) then `weigh` then
 * `rankWithFloor` (the list is rescored and reordered).
 *
 * WHAT THIS DOES AND DOES NOT CLAIM
 *
 * Claimed: on this machine, in this Node build, the arithmetic for 60 rows x 6
 * axes costs what the reported median and p95 say it costs, and it sits orders
 * of magnitude below a 16 ms frame -- so the frame budget is spent on layout,
 * paint and FLIP, not on scoring. That headroom is a property of the
 * algorithm: `weigh` is O(models x weighted axes) and `rankWithFloor` is one
 * O(n log n) sort over 60 rows. No machine that can run a browser at all will
 * turn ~60 x 6 multiply-adds into 16 ms.
 *
 * NOT claimed: that the numbers printed here reproduce on your machine, or in
 * CI, or under a cold JIT. They are wall-clock samples from one process on one
 * box and they move with CPU, thermal state and GC. That is exactly why the
 * assertions below are loose and the precise figures are printed rather than
 * asserted -- a tight assertion here would only measure the CI runner's mood.
 *
 * NOT measured here: rendering. The DOM cost of the same input is measured in
 * `e2e/rerank.perf.spec.ts`, in a real browser.
 */
import { describe, expect, it } from 'vitest';

import { rankWithFloor, renormalise, weigh, type AxesByModel } from '../src/lib/rank/weigh';

/** the fixture's six axis names, so this case is shaped like the shared one */
const AXES = ['a', 'b', 'c', 'd', 'e', 'f'] as const;
const ROWS = 60;

/**
 * A deterministic spread over 0..1 -- no Math.random, because a benchmark that
 * ranks a different list every run cannot be compared with itself. An integer
 * hash gives values that are scattered rather than sorted, so the sort in
 * `rankWithFloor` does real work instead of walking an already-ordered array.
 */
function spread(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

/**
 * 60 models, six axes each, values across 0..1. Two models carry a null axis
 * so the unmeasured-value path is exercised: it must contribute 0 to the score
 * and cost that axis's share of confidence, which is what pushes those rows
 * through the `rankWithFloor` floor branch rather than the fast one.
 */
function synthetic(rows: number): AxesByModel {
  const out: AxesByModel = {};
  for (let i = 0; i < rows; i++) {
    const model = `vendor-${String(i % 7)}/model-${String(i).padStart(3, '0')}`;
    const axes: Record<string, { value: number | null; coverage: number }> = {};
    AXES.forEach((axis, a) => {
      // rows 17 and 41 are missing one axis apiece
      const missing = (i === 17 && a === 2) || (i === 41 && a === 4);
      axes[axis] = {
        value: missing ? null : spread(i * 6 + a + 1),
        coverage: missing ? 0 : 0.6 + spread(i * 6 + a + 101) * 0.4
      };
    });
    out[model] = axes;
  }
  return out;
}

/** all six axes weighted, as the `coder` profile weights all six of its own */
const WEIGHTS: Record<string, number> = {
  a: 0.45,
  b: 0.2,
  c: 0.1,
  d: 0.1,
  e: 0.1,
  f: 0.05
};
const MIN_CONFIDENCE = 0.75;

function quantile(sorted: number[], q: number): number {
  const at = Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1));
  return sorted[at];
}

describe('one slider input, as pure computation', () => {
  it(`costs far under a 16 ms frame on a ${String(ROWS)}-row list`, () => {
    const axesByModel = synthetic(ROWS);
    expect(Object.keys(axesByModel)).toHaveLength(ROWS);

    // one re-rank == what a single `input` on a WeightSlider triggers
    let sink = 0;
    const rerank = (value: number) => {
      const weights = renormalise(WEIGHTS, 'a', value, new Set());
      const ranked = rankWithFloor(weigh(axesByModel, weights), MIN_CONFIDENCE);
      // consume the result so nothing above can be optimised away
      sink += ranked.length + ranked[0].score;
      return ranked;
    };

    // the list really does reorder as the weight moves, or this measures nothing
    const low = rerank(0.05).map((row) => row.model_id);
    const high = rerank(0.95).map((row) => row.model_id);
    expect(high).not.toEqual(low);
    expect(low).toHaveLength(ROWS);

    // warm the JIT: the first calls are compilation, not steady-state cost
    for (let i = 0; i < 2_000; i++) rerank(0.2 + (i % 600) / 1000);

    const runs = 5_000;
    const samples: number[] = new Array(runs);
    for (let i = 0; i < runs; i++) {
      // a fresh value each time, as a dragging finger would send
      const value = 0.2 + (i % 600) / 1000;
      const started = performance.now();
      rerank(value);
      samples[i] = performance.now() - started;
    }
    expect(sink).toBeGreaterThan(0);

    const sorted = [...samples].sort((a, b) => a - b);
    const median = quantile(sorted, 0.5);
    const p95 = quantile(sorted, 0.95);
    const worst = sorted[sorted.length - 1];

    // the precise numbers live here, in the output, not in the assertions
    console.log(
      [
        `pure re-rank, ${String(ROWS)} rows x ${String(AXES.length)} axes, ${String(runs)} runs:`,
        `  median ${median.toFixed(4)} ms`,
        `  p95    ${p95.toFixed(4)} ms`,
        `  max    ${worst.toFixed(4)} ms`,
        `  budget 16.0000 ms  (median is ${(16 / Math.max(median, 1e-9)).toFixed(0)}x under)`
      ].join('\n')
    );

    // deliberately loose: a slow, loaded CI runner is allowed to be 100x worse
    // than this machine and still pass, because the claim under test is "well
    // inside a frame", not "0.02 ms".
    expect(median).toBeLessThan(4);
    expect(p95).toBeLessThan(8);
    // no single sample, GC pause included, may blow the frame budget
    expect(worst).toBeLessThan(16);
  });
});
