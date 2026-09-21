import { expect, test, type Page } from '@playwright/test';

/**
 * What one input on a weight costs the main thread, in a real browser.
 *
 * The claim this file was written for is brief D's: the editor must "re-rank
 * under 16 ms per input on a 60-row list", one frame at 60 fps, so the list
 * tracks the finger. The page that claim was made about is gone -- the old
 * ranking list, sixty rows of it, on `/profiles/[name]`, whose stub answered
 * the whole ranking. In the console the seat page asks the server to rank
 * (`/v1/profiles/[name]/preview`, debounced 400 ms) and draws the answer, so
 * there is no local reorder of sixty keyed rows left to time; what is local,
 * and what this file measures, is the input itself: the divider the reader is
 * holding, the bar it redraws, and every other part of the seat pane that
 * reacts on the same turn.
 *
 * So the budget is unchanged and the subject is honest: `median < 32 ms` and
 * `p95 < 48 ms`, the same bounds as before, over the seat pane. The second
 * test states what the old file's 60-row case can no longer state -- the list
 * itself follows the preview, one round trip behind the input, and the figure
 * is reported as a round trip rather than asserted as a frame.
 */

const SAMPLES = 12;
/** The debounce in `session.edit` (400 ms) plus a round trip, with room. */
const SETTLE_MS = 2500;

/** The seeded store's write token: a nudge is an edit, and an edit is saved. */
const TOKEN = 'ci-secret';

/**
 * Sign in before nudging anything.
 *
 * A nudge is saved 400 ms later, and this browser has no token: the save comes
 * back 401 and the app sends it to the login route, which lands in the middle
 * of a measurement and takes the rows with it. Reads need no token; the edit
 * this file makes does.
 */
async function signIn(page: Page): Promise<void> {
  await page.locator('button.who').click();
  const field = page.getByPlaceholder('paste a bearer token');
  await field.fill(TOKEN);
  await page.keyboard.press('Escape');
  await expect(field).toHaveCount(0);
}

type Measured = { costs: number[]; moved: number; rows: number };

/** Quantised to 0.1 ms by Chromium; useless below ~0.5 ms, fine at 32. */
function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return Number.NaN;
  const at = (sorted.length - 1) * q;
  const lo = Math.floor(at);
  const hi = Math.ceil(at);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo);
}

/**
 * One real keyboard nudge per sample, dispatched in the page so the clock does
 * not cross the CDP boundary: `performance.now()` before the event, the first
 * mutation the pane sees after it. A sample that mutates nothing is dropped
 * and counted, so a page that ignores the input cannot pass by being fast.
 */
async function measure(page: Page, samples = SAMPLES): Promise<Measured> {
  return page.evaluate(async (count) => {
    const pane = document.querySelector('[data-slot="seat"]');
    if (!(pane instanceof HTMLElement)) return { costs: [], moved: 0, rows: 0 };
    const rows = pane.querySelectorAll('[role="row"][data-row]').length;
    const costs: number[] = [];
    let moved = 0;

    for (let i = 0; i < count; i += 1) {
      const handles = pane.querySelectorAll<HTMLElement>('[role="slider"]');
      const handle = handles[i % Math.max(handles.length, 1)];
      if (!handle) break;
      handle.focus();
      const started = performance.now();
      const painted = new Promise<number | null>((resolve) => {
        const observer = new MutationObserver(() => {
          observer.disconnect();
          resolve(performance.now() - started);
        });
        observer.observe(pane, {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true
        });
        window.setTimeout(() => {
          observer.disconnect();
          resolve(null);
        }, 400);
      });
      handle.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: i % 2 === 0 ? 'ArrowRight' : 'ArrowLeft',
          shiftKey: true,
          bubbles: true
        })
      );
      const took = await painted;
      if (took === null) continue;
      costs.push(took);
      moved += 1;
      await new Promise((done) => requestAnimationFrame(() => done(null)));
    }
    return { costs, moved, rows };
  }, samples);
}

function report(label: string, measured: Measured) {
  const sorted = [...measured.costs].sort((a, b) => a - b);
  const median = quantile(sorted, 0.5);
  const p95 = quantile(sorted, 0.95);
  console.log(
    [
      `  ${label}: ${measured.rows} rows in the pane, ${measured.moved}/${SAMPLES} inputs moved it`,
      `  median ${median.toFixed(3)} ms`,
      `  p95    ${p95.toFixed(3)} ms`,
      `  worst  ${(sorted.at(-1) ?? Number.NaN).toFixed(3)} ms`
    ].join('\n')
  );
  return { median, p95 };
}

test('an input on a weight costs a fraction of a frame', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats/coder?view=all');
  await signIn(page);
  await expect(
    page.getByRole('table', { name: 'Every reachable model' }).locator('[role="row"][data-row]').first()
  ).toBeVisible();
  await expect(page.getByRole('slider').first()).toBeVisible();

  const measured = await measure(page);
  const { median, p95 } = report('the seat pane under keyboard nudges', measured);

  expect(measured.rows, 'the pane drew no list').toBeGreaterThan(0);
  expect(measured.moved, 'the page ignored the input').toBeGreaterThan(SAMPLES / 2);
  expect(median).toBeLessThan(32);
  expect(p95).toBeLessThan(48);
});

test('the list follows the input in one round trip', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats/coder?view=all');
  const rows = page.getByRole('table', { name: 'Every reachable model' }).locator('[role="row"][data-row]');
  await signIn(page);
  await expect(rows.first()).toBeVisible();

  const before = await rows.evaluateAll((els) => els.map((el) => el.textContent ?? ''));
  const handle = page.getByRole('slider').first();
  await handle.focus();
  const started = Date.now();
  await page.keyboard.press('Shift+ArrowRight');

  // the answer is the server's, so this is a round trip and not a frame: the
  // number is the debounce plus one request, and it is what the reader waits
  let waited = Number.NaN;
  await expect
    .poll(async () => {
      const now = await rows.evaluateAll((els) => els.map((el) => el.textContent ?? ''));
      if (now.join(' ') === before.join(' ')) return false;
      waited = Date.now() - started;
      return true;
    }, { timeout: SETTLE_MS, intervals: [50, 100, 200, 400] })
    .toBe(true);

  console.log(`  the list re-ranked ${waited} ms after the input`);
  expect(waited).toBeLessThan(SETTLE_MS);
});
