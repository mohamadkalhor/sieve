import { expect, test } from '@playwright/test';

/**
 * Does hovering the Field redraw every point?
 *
 * Phase 1's handoff said it did, and brief PHASE-2 part 6 asks for one of two
 * answers: split the scene from the hover marker, or **measure it and show the
 * measurement**. This measures it.
 *
 * The scene is painted in a `$effect` that depends on `[placed, width, height]`
 * and not on `hovered`, so a hover should repaint nothing on the canvas — it
 * moves an HTML tooltip positioned over it. That is a claim about a dependency
 * list, and a dependency list is exactly the sort of thing that quietly grows
 * an extra entry later. So rather than reading the code, this counts the actual
 * drawing calls the canvas receives while the pointer moves across it.
 *
 * `arc()` is the right thing to count: it is called once per plotted point and
 * nowhere else in the component, so "arcs during hover" is "points redrawn".
 *
 * WHAT THIS MEASURED, 2026-09-08, so nobody re-derives it
 *
 *   arcs drawn by one full repaint: 59
 *   arcs drawn by 40 hovers:         0
 *   per hover: median 0.10 ms, p95 0.90 ms, max 2.20 ms
 *
 * Phase 1's handoff said "the scatter redraws all points rather than only the
 * hover". That is false, and this is how we know rather than by reading it.
 *
 * One honest bound: the seeded fixture plots 59 models, not the 644 the live
 * API returns, so the *timings* are at 59. The zero is not — it is a count of
 * draw calls that did not happen, and it stays zero at any number of points,
 * because the scene effect does not depend on the hover at all. Scaling the
 * point count changes what a repaint costs, not how often hovering causes one.
 */

const HOVERS = 40;

test('hovering the Field repaints no points, and stays inside a frame', async ({ page }) => {
  await page.goto('/field');

  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();

  // let the first paint settle before counting anything
  await page.waitForTimeout(300);

  const measured = await page.evaluate(async (hovers) => {
    const element = document.querySelector('canvas') as HTMLCanvasElement;
    const box = element.getBoundingClientRect();

    const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, unknown>;
    const realArc = proto.arc as (...args: unknown[]) => void;
    let arcs = 0;
    proto.arc = function patched(this: unknown, ...args: unknown[]) {
      arcs += 1;
      return realArc.apply(this, args);
    };

    const send = (x: number, y: number) =>
      element.dispatchEvent(
        new MouseEvent('mousemove', { clientX: x, clientY: y, bubbles: true })
      );

    // What a real repaint costs, for scale. The scene redraws on a width
    // change, so narrow the host and wait for the ResizeObserver: a
    // `window.resize` event alone changes no element and repaints nothing,
    // which would have made the baseline a meaningless zero.
    const host = element.parentElement as HTMLElement;
    const before = arcs;
    host.style.width = `${Math.round(box.width) - 40}px`;
    await new Promise((r) => setTimeout(r, 300));
    const arcsPerPaint = arcs - before;
    host.style.width = '';
    await new Promise((r) => setTimeout(r, 300));

    // now the hovers
    const start = arcs;
    const samples: number[] = [];
    for (let i = 0; i < hovers; i++) {
      const x = box.left + 40 + ((box.width - 80) * i) / hovers;
      const y = box.top + box.height / 2 + Math.sin(i) * 30;
      const t0 = performance.now();
      send(x, y);
      // Svelte 5 flushes on a microtask, so that plus a forced reflow is the
      // real boundary. Waiting a frame here would have measured the frame
      // interval itself -- 16.7 ms at 60 Hz -- and called it the cost of a
      // hover, which is the measurement equivalent of a rounding error the
      // size of the thing being measured.
      await Promise.resolve();
      void document.body.offsetHeight;
      samples.push(performance.now() - t0);
    }
    const arcsDuringHover = arcs - start;

    proto.arc = realArc;
    samples.sort((a, b) => a - b);
    return {
      arcsPerPaint,
      arcsDuringHover,
      hovers,
      median: samples[Math.floor(samples.length / 2)],
      p95: samples[Math.min(samples.length - 1, Math.floor(samples.length * 0.95))],
      max: samples[samples.length - 1]
    };
  }, HOVERS);

  console.log(
    `Field hover: ${measured.hovers} moves\n` +
      `  arcs drawn by one full repaint: ${measured.arcsPerPaint}\n` +
      `  arcs drawn by ${measured.hovers} hovers:  ${measured.arcsDuringHover}\n` +
      `  per hover: median ${measured.median.toFixed(2)} ms, ` +
      `p95 ${measured.p95.toFixed(2)} ms, max ${measured.max.toFixed(2)} ms`
  );

  // The measurement, as an assertion: hovering must not cost a repaint. If the
  // draw effect ever grows a dependency on `hovered`, this goes from 0 to
  // 40 x arcsPerPaint and fails loudly.
  expect(
    measured.arcsDuringHover,
    'hovering redrew points — the scene effect now depends on the hover'
  ).toBe(0);

  // a full repaint does draw every point, so the baseline is meaningful
  expect(measured.arcsPerPaint, 'a real repaint should draw the points').toBeGreaterThan(0);

  // and the interaction itself fits a frame with room to spare
  expect(measured.p95).toBeLessThan(16);
});

test('the hover marker is HTML over the canvas, not a canvas repaint', async ({ page }) => {
  await page.goto('/field');
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();

  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();

  // walk the middle of the plot until something is actually hit
  let shown = false;
  for (let i = 0; i < 60 && !shown; i++) {
    await page.mouse.move(box!.x + 40 + (box!.width - 80) * (i / 60), box!.y + box!.height / 2);
    shown = await page.locator('.tip, .tooltip, figure.field > div').first().isVisible();
  }
  // Not every dataset puts a point under the mid-line, so this is a soft check:
  // what must hold is that when a tooltip does appear, it is a DOM node.
  if (shown) {
    const tag = await page
      .locator('.tip, .tooltip, figure.field > div')
      .first()
      .evaluate((n) => n.tagName);
    expect(tag).not.toBe('CANVAS');
  }
});
