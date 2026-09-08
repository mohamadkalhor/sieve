import { expect, test } from '@playwright/test';

/**
 * Pulse, the sixth screen in PLAN §8 and the only one that had never been built.
 *
 * It reads `/v1/health`, which reads telemetry, which is empty on a fresh
 * store -- so the interesting states are both of them: nothing reported yet,
 * and something reported. The empty state matters as much as the full one,
 * because "no calls" and "unhealthy" are opposite facts and the screen must
 * never let them look alike.
 */

test('with no telemetry it says so, rather than showing perfect health', async ({ page }) => {
  await page.goto('/pulse');
  await expect(page.getByRole('heading', { name: 'Pulse', level: 1 })).toBeVisible();
  await expect(page.getByText('Nothing has reported a call yet')).toBeVisible();
});

test('posted calls appear with their rate-limited share and latencies', async ({ page, request }) => {
  const now = Date.now();
  const models = await (await request.get('/v1/inventory')).json();
  const model = models.find((m: { model_id: string | null }) => m.model_id)?.model_id;
  expect(model, 'the seeded store has at least one reachable model').toBeTruthy();

  const events = Array.from({ length: 25 }, (_, i) => ({
    model,
    ok: i % 5 !== 0,
    status: i % 5 === 0 ? 429 : 200,
    latency_ms: 120 + i * 3,
    tokens_out: 800,
    at: new Date(now - i * 60_000).toISOString()
  }));

  const posted = await request.post('/v1/telemetry', {
    data: events,
    headers: { authorization: 'Bearer ci-secret' }
  });
  expect(posted.status()).toBe(200);

  await page.goto('/pulse');
  const row = page.locator('tbody tr', { hasText: model as string });
  await expect(row).toBeVisible();
  await expect(row).toContainText('20.0%'); // one call in five was throttled
  await expect(row.locator('svg')).toBeVisible(); // the sparkline the route was built for

  // the health number has to have moved off 1.00, or telemetry is decoration
  const health = await row.locator('.health').innerText();
  expect(Number(health)).toBeLessThan(1);
});

test('no horizontal scroll at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/pulse');
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(overflow).toBe(false);
});
