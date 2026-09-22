import { expect, test, type Locator, type Page } from '@playwright/test';

const SUMMARY = 'status summary '.repeat(40).slice(0, 400);
const PHONE = { width: 375, height: 812 };
const DESKS = [
  { width: 1024, height: 768 },
  { width: 1040, height: 768 },
  { width: 1075, height: 768 },
  { width: 1100, height: 768 },
  { width: 1440, height: 900 }
];

type Box = { x: number; y: number; width: number; height: number };

async function box(locator: Locator, what: string): Promise<Box> {
  const measured = await locator.boundingBox();
  if (!measured) throw new Error(`${what} is not drawn`);
  return measured;
}

async function stubStatus(page: Page): Promise<void> {
  await page.route('**/v1/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pulled_at: '2026-09-22T12:00:00Z',
        ran_at: '2026-09-22T12:00:00Z',
        schedule: 'hourly',
        sources_enabled: 5,
        telemetry_calls: 14852,
        telemetry_at: '2026-09-22T12:00:00Z',
        reachable: 477,
        unscored: 333,
        runs: {
          running: null,
          last: {
            id: 'long-summary',
            step: 'full',
            requested_by: 'schedule',
            started: '2026-09-22T11:59:00Z',
            finished: '2026-09-22T12:00:00Z',
            running: false,
            ok: false,
            summary: SUMMARY,
            error: 'one step failed',
            seconds: 60,
            has_log: true
          },
          last_by_step: {}
        },
        schedules: [
          {
            step: 'full',
            mode: 'hourly',
            at_minute: 0,
            at_time: '00:00',
            timezone: 'UTC',
            last_fired: '2026-09-22T12:00:00Z',
            next_fire: '2099-09-22T13:00:00Z'
          }
        ]
      })
    })
  );
}

async function openSeat(page: Page, viewport: { width: number; height: number }): Promise<Locator> {
  await page.setViewportSize(viewport);
  await page.goto('/seats/coder');
  const status = page.locator('footer[aria-label="Status"]');
  await expect(status).toBeVisible();
  await expect(status.locator('[data-key="run"]')).toContainText('run failed');
  return status;
}

test('a long run summary yields to every status item on a desk', async ({ page }) => {
  await stubStatus(page);

  for (const viewport of DESKS) {
    const status = await openSeat(page, viewport);
    const topbar = page.locator('header.topbar');
    const summary = status.getByRole('link', { name: SUMMARY });
    const unscored = status.getByRole('link', { name: '333 unscored' });
    const next = status.locator('[data-key="next"]');
    const windowWidth = await page.evaluate(() => window.innerWidth);

    await expect(summary).toHaveAttribute('title', SUMMARY);
    await expect(summary).toHaveAttribute('href', '/runs');
    await expect(unscored).toBeVisible();
    await expect(next).toBeVisible();
    const summaryStyle = await summary.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        display: style.display,
        textOverflow: style.textOverflow,
        truncated: element.scrollWidth > element.clientWidth
      };
    });
    expect(summaryStyle.textOverflow).toBe('ellipsis');
    expect(summaryStyle.display).not.toBe('flex');
    expect(summaryStyle.truncated, `the ${viewport.width}px summary is not truncated`).toBe(true);

    for (const [locator, what] of [
      [topbar, 'the top bar'],
      [status, 'the status bar']
    ] as const) {
      const where = await box(locator, what);
      expect(where.x + where.width, `${what} runs past the window`).toBeLessThanOrEqual(
        windowWidth + 1
      );
    }
    for (const item of await status.locator(':scope > *').all()) {
      if (!(await item.isVisible())) continue;
      const where = await box(item, 'a status item');
      expect(where.x + where.width, 'a status item runs past the window').toBeLessThanOrEqual(
        windowWidth + 1
      );
    }
    expect(await next.evaluate((element) => element.scrollWidth)).toBeLessThanOrEqual(
      await next.evaluate((element) => element.clientWidth)
    );

    await unscored.click();
    await expect(page).toHaveURL(/\/unscored$/);
  }
});

test('the phone still shows only the run state and unscored link', async ({ page }) => {
  await stubStatus(page);
  const status = await openSeat(page, PHONE);

  await expect(status.locator('[data-key="summary"]')).toBeHidden();
  await expect(status.locator('[data-key="run"]')).toBeVisible();
  await expect(status.getByRole('link', { name: '333 unscored' })).toBeVisible();
  await expect(status.locator('[data-key="reachable"]')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(PHONE.width);
});
