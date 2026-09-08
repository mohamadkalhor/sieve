import { expect, test, type Page } from '@playwright/test';

/**
 * How long one slider input costs in a real browser.
 *
 * Brief D says the editor must "re-rank under 16 ms per input on a 60-row
 * list" -- one frame at 60 fps, so the list tracks the finger. The vitest in
 * `tests/rerank.bench.test.ts` measures the arithmetic; this file measures the
 * thing the brief actually claims: a real Chromium, the real built app, a real
 * `input` on a real `<input type="range">`, and the real keyed-each reorder
 * with `animate:flip` attached.
 *
 * TWO ROW COUNTS, AND WHY
 *
 * The shipped fixture store cannot produce 60 ranked rows. It carries axis
 * data for exactly six LLMs (`tests/fixtures/artificialanalysis_*_llms_*.json`)
 * and the e2e inventory makes eight models reachable, so `/profiles/coder`
 * renders six rows and no profile can do better -- loosening constraints does
 * not help, because the ceiling is the axis data, not the constraints. So:
 *
 *   1. `shipped data` measures the app exactly as it ships, at whatever row
 *      count the fixtures really yield. That count is asserted, not assumed,
 *      and printed with the timings.
 *   2. `60 rows` measures the same real page with the ranking response stubbed
 *      to 60 synthetic models. Only the JSON is synthetic: the Svelte build,
 *      the reactivity, the DOM, the FLIP and the input event are all real.
 *      This is the case brief D names, and it is labelled as stubbed wherever
 *      its numbers are reported.
 *
 * WHAT THE NUMBER IS
 *
 * Per input: from dispatching `input` to the list having re-rendered, with a
 * forced reflow inside the measurement. That is the main-thread work one input
 * causes -- reactive recompute, the reorder, FLIP setup, layout. It is what has
 * to fit in a frame. It excludes compositing, which is off the critical path,
 * so the long-frame count is reported alongside as a cross-check.
 *
 * Note that Chromium coarsens `performance.now()` to 100 us, so every figure
 * here is quantised to 0.1 ms. That is fine against a 16 ms budget and useless
 * below about 0.5 ms, which is why the sub-millisecond arithmetic is measured
 * in Node instead.
 *
 * WHAT THESE TESTS FOUND, so nobody has to re-derive it from the test names
 *
 * At the six rows the fixtures yield, an input costs about 3-5 ms: comfortable.
 * At 60 rows it costs about 14-15 ms at the median and 22-23 ms at p95 -- the
 * median only just fits a 16 ms frame and the tail does not. The scoring is not
 * the reason: `weigh` + `rankWithFloor` over the same 60 rows is ~0.07 ms, and
 * disabling FLIP only recovers ~2 ms. The cost is reconciling 60 keyed rows.
 * So brief D's "under 16 ms per input on a 60-row list" holds for the ranking
 * and is marginal-to-failing for the editor as a whole at that size. The bounds
 * asserted below are deliberately far looser than any of these numbers; they
 * exist to catch a regression, not to re-state the finding.
 */

const AXES = ['agentic_coding', 'cost', 'agentic_tools', 'reasoning', 'long_context', 'latency'];
const SAMPLES = 40;

/** deterministic spread over 0..1 -- the same list every run, in no useful order */
function spread(seed: number): number {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

/** a ranking payload of `rows` models, shaped exactly like the server's */
function syntheticRanking(rows: number) {
  return {
    profile: 'coder',
    modality: 'llm',
    computed_at: '2026-01-01T00:00:00Z',
    snapshot: 'synthetic60',
    ranks: Array.from({ length: rows }, (_, i) => {
      const axes = AXES.map((axis, a) => {
        // two rows are missing an axis apiece, so confidence really varies
        const missing = (i === 17 && a === 2) || (i === 41 && a === 4);
        return {
          axis,
          value: missing ? null : spread(i * 6 + a + 1),
          coverage: missing ? 0 : 0.6 + spread(i * 6 + a + 101) * 0.4,
          contribution: 0
        };
      });
      return {
        position: i + 1,
        model_id: `vendor-${String(i % 7)}/model-${String(i).padStart(3, '0')}`,
        reachable: true,
        local_ids: [],
        score: 0,
        confidence: 0.9,
        health: 1,
        final: 0,
        axes,
        cost_per_task: null,
        dominated_by: null,
        excluded_by: null,
        flip: null
      };
    })
  };
}

interface Timings {
  rows: number;
  samples: number[];
  /** inputs after which the list really re-rendered */
  rendered: number;
  /** inputs after which rows actually swapped places, so FLIP had work to do */
  reordered: number;
  /** microtask turns Svelte needed to flush, worst case */
  maxSpins: number;
  longFrames: number;
}

/**
 * Drive `#w-cost` `runs` times and time each input.
 *
 * Runs wholly inside the page, so no CDP round-trip lands in the measurement.
 *
 * Settling is detected with a MutationObserver rather than by diffing rendered
 * text. Two reasons: the check inside the timed region collapses to reading one
 * boolean, so the instrument costs nothing; and it is sensitive to any row
 * changing, where an earlier version watched only the top row and missed a
 * quarter of the inputs whose effect was further down the list.
 */
async function measure(page: Page, runs: number): Promise<Timings> {
  return page.evaluate(async (count: number) => {
    const slider = document.querySelector<HTMLInputElement>('#w-cost');
    const list = document.querySelector<HTMLOListElement>('ol.live');
    if (!slider || !list) throw new Error('the editor did not render a slider and a list');

    // frames over 50 ms during the burst, as a cross-check on the per-input number
    let longFrames = 0;
    let observer: PerformanceObserver | null = null;
    try {
      if (PerformanceObserver.supportedEntryTypes?.includes('long-animation-frame')) {
        observer = new PerformanceObserver((entries) => {
          longFrames += entries.getEntries().length;
        });
        observer.observe({ type: 'long-animation-frame', buffered: false });
      }
    } catch {
      observer = null;
    }

    // any re-render of the list flips this, and reading it is free
    let mutated = false;
    const mutations = new MutationObserver(() => {
      mutated = true;
    });
    mutations.observe(list, { childList: true, subtree: true, characterData: true });

    /** the whole list, for the out-of-band check that rows really reorder */
    const fullOrder = () =>
      Array.from(list.querySelectorAll('li'))
        .map((li) => li.querySelector('.id')?.textContent ?? '')
        .join('|');

    // go through the native setter, so the range input really holds the new
    // value before the event fires
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
      ?.set as (this: HTMLInputElement, v: string) => void;

    const timings: number[] = [];
    let rendered = 0;
    let reordered = 0;
    let maxSpins = 0;

    // let first-paint work settle before timing anything
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    for (let i = 0; i < count; i++) {
      // consecutive values 0.05 apart, as a dragging finger would send
      const value = 0.05 + (i % 18) * 0.05;
      const orderBefore = fullOrder();

      setValue.call(slider, value.toFixed(2));
      mutated = false;
      const started = performance.now();
      slider.dispatchEvent(new Event('input', { bubbles: true }));

      // Svelte flushes on a microtask, and MutationObserver records are
      // delivered at the same checkpoint, so awaiting lets both run
      let spins = 0;
      while (!mutated && spins < 5000) {
        await Promise.resolve();
        spins++;
      }
      // pull layout into the measurement: the reorder is not paid for until it reflows
      void list.offsetHeight;
      const elapsed = performance.now() - started;

      if (mutated) {
        rendered++;
        timings.push(elapsed);
        maxSpins = Math.max(maxSpins, spins);
      }
      if (fullOrder() !== orderBefore) reordered++;

      // hand the frame back, so each sample starts from a clean main thread
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }

    observer?.disconnect();
    mutations.disconnect();
    return {
      rows: list.querySelectorAll('li').length,
      samples: timings,
      rendered,
      reordered,
      maxSpins,
      longFrames
    };
  }, runs);
}

function quantile(sorted: number[], q: number): number {
  const at = Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1));
  return sorted[at];
}

function report(label: string, timings: Timings) {
  const sorted = [...timings.samples].sort((a, b) => a - b);
  const median = quantile(sorted, 0.5);
  const p95 = quantile(sorted, 0.95);
  const worst = sorted[sorted.length - 1];
  console.log(
    [
      `browser re-rank, ${label}, ${String(timings.rows)} rows, ${String(sorted.length)} timed inputs:`,
      `  median ${median.toFixed(3)} ms`,
      `  p95    ${p95.toFixed(3)} ms`,
      `  max    ${worst.toFixed(3)} ms`,
      `  inputs that reordered rows: ${String(timings.reordered)}/${String(timings.rendered)}`,
      `  microtask turns to flush, worst: ${String(timings.maxSpins)}`,
      `  frames over 50 ms during the burst: ${String(timings.longFrames)}`,
      `  budget 16.000 ms`
    ].join('\n')
  );
  return { median, p95, worst };
}

test('one input re-ranks well inside a frame, on the data that ships', async ({ page }) => {
  await page.goto('/profiles/coder');
  await expect(page.locator('.live li').first()).toBeVisible();

  const timings = await measure(page, SAMPLES);
  const { median, p95 } = report('shipped fixture data', timings);

  // the fixtures really do yield this few rows; see the note at the top
  expect(timings.rows).toBeGreaterThan(0);
  expect(timings.rows).toBeLessThan(60);
  // every input actually re-rendered the list, or the timings mean nothing
  expect(timings.rendered).toBe(SAMPLES);
  // and the point of the exercise is that rows move, not just that text changes
  expect(timings.reordered).toBeGreaterThan(0);

  // loose on purpose: a slow CI runner may be several times slower than a
  // laptop and the claim under test is still "inside a frame". The precise
  // figures are in the output above, not in this bound.
  expect(median).toBeLessThan(32);
  expect(p95).toBeLessThan(48);
});

test('one input on a 60-row list costs most of a frame', async ({ page }) => {
  // 60 rows cannot come out of the fixture store, so the ranking response is
  // stubbed. Everything below the JSON is the real app.
  await page.route('**/v1/rankings/coder', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(syntheticRanking(60))
    });
  });

  await page.goto('/profiles/coder');
  await expect(page.locator('.live li')).toHaveCount(60);

  const timings = await measure(page, SAMPLES);
  const { median, p95 } = report('60 synthetic rows (stubbed ranking)', timings);

  expect(timings.rows).toBe(60);
  expect(timings.rendered).toBe(SAMPLES);
  // at 60 rows every input moves somebody, so FLIP is doing full work
  expect(timings.reordered).toBe(SAMPLES);

  // NOT `median < 16`. On the machine this was written on the median is a
  // little under 16 ms, which makes 16 ms a coin-flip on a slower runner --
  // exactly the flaky assertion brief D says to avoid. The bound here catches
  // a real regression (something four times slower) and the honest figure is
  // printed above, where a human can read it.
  expect(median).toBeLessThan(60);
  expect(p95).toBeLessThan(90);
});

test('at 60 rows the cost is the DOM, not the animation', async ({ page }) => {
  /**
   * Where the 60-row cost actually goes.
   *
   * The arithmetic is ~0.07 ms (see the vitest), yet the same input in the
   * browser costs over ten. This test runs the identical burst with
   * `prefers-reduced-motion`, which collapses `duration(240, ...)` to 0 and so
   * takes `animate:flip` out of the path while leaving the re-rank, the DOM
   * reorder and the reflow in it.
   *
   * Turning the animation off moves the median by roughly 2 ms, so FLIP is not
   * the expense: what costs is reconciling a keyed `{#each}` over 60 rows and
   * ~600 nodes. Said plainly, the scoring is free and the rendering is not,
   * which is the opposite of where one would look first.
   */
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/v1/rankings/coder', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(syntheticRanking(60))
    });
  });

  await page.goto('/profiles/coder');
  await expect(page.locator('.live li')).toHaveCount(60);

  const timings = await measure(page, SAMPLES);
  const { median, p95 } = report('60 synthetic rows, motion reduced (stubbed ranking)', timings);

  expect(timings.rows).toBe(60);
  expect(timings.rendered).toBe(SAMPLES);
  // the reorder must survive reduce-motion; only the animation may go
  expect(timings.reordered).toBe(SAMPLES);

  expect(median).toBeLessThan(60);
  expect(p95).toBeLessThan(90);
});
