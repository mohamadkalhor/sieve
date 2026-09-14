import { expect, test, type Page } from '@playwright/test';

/**
 * AMS-31: the status box is on every page, it has a row per step, and Run now
 * really runs something.
 *
 * The old box read a cadence out of `sieve.toml` and could not start anything,
 * so "the loop is stuck" and "the loop is fine" looked the same. These check
 * the two facts that matter: four rows with a schedule each, and a button that
 * leaves a row in the runs table behind it.
 */

const TOKEN = 'ci-secret';

/** the box is in the layout, so it is on whatever page you opened */
const box = (page: Page) => page.locator('section[aria-label="Runs"]');

async function withToken(page: Page, path = '/connectors') {
  await page.goto(path);
  const field = page.locator('label.token input');
  await expect(field).toBeVisible();
  await field.fill(TOKEN);
}

test('the status box is on Connectors and Runs, and nowhere else', async ({ page }) => {
  for (const path of ['/connectors', '/runs']) {
    await page.goto(path);
    await expect(box(page).locator('li')).toHaveCount(4);
  }

  // it used to sit above every screen, which made Profiles a control panel
  // with a list under it. These are screens you come to read.
  for (const path of ['/profiles', '/profiles/coder', '/axes', '/guide', '/field']) {
    await page.goto(path);
    await expect(page.locator('h1')).toBeVisible();
    await expect(box(page)).toHaveCount(0);
  }

  await page.goto('/connectors');
  const rows = box(page).locator('li');
  await expect(rows.nth(0)).toContainText('Full run');
  await expect(rows.nth(1)).toContainText('Pull sources');
  await expect(rows.nth(2)).toContainText('Harvest connectors');
  await expect(rows.nth(3)).toContainText('Ship profiles');

  // the seed the retired timer left behind: full, daily, and a next moment
  await expect(box(page).locator('#mode-full')).toHaveValue('daily');
  await expect(rows.nth(0)).toContainText('next');
  await expect(rows.nth(1)).toContainText('no schedule');
});

test('Run now on pull_sources produces a runs row', async ({ page, request }) => {
  await withToken(page);

  const before = await (await request.get('/v1/runs?limit=50')).json();
  expect(Array.isArray(before)).toBe(true);

  await box(page).locator('button[data-step="pull_sources"]').click();

  // the run itself is a background thread; wait for the row rather than a pixel
  await expect
    .poll(
      async () => {
        const rows = await (await request.get('/v1/runs?step=pull_sources&limit=5')).json();
        return Array.isArray(rows) && rows.length > 0 && rows[0].running === false
          ? rows[0].ok
          : null;
      },
      { timeout: 60_000, message: 'the run never finished' }
    )
    .toBe(true);

  const after = await (await request.get('/v1/runs?limit=50')).json();
  expect(after.length).toBeGreaterThan(before.length);
  expect(after[0].step).toBe('pull_sources');
  expect(after[0].summary).toContain('rows pulled');

  // and the record screen shows it
  await page.goto('/runs');
  await expect(page.locator('ul.runs li').first()).toContainText('Pull sources');
});

test('a schedule can be changed from the box, and the API agrees', async ({ page, request }) => {
  await withToken(page);

  await box(page).locator('#mode-harvest_connectors').selectOption('hourly');
  await expect
    .poll(async () => {
      const rows = await (await request.get('/v1/schedules')).json();
      return rows.find((r: { step: string }) => r.step === 'harvest_connectors')?.mode;
    })
    .toBe('hourly');
  await expect(box(page).locator('li').nth(2)).toContainText('next');

  await box(page).locator('#mode-harvest_connectors').selectOption('off');
  await expect
    .poll(async () => {
      const rows = await (await request.get('/v1/schedules')).json();
      return rows.find((r: { step: string }) => r.step === 'harvest_connectors')?.mode;
    })
    .toBe('off');
});

test('without a token the box says so and the buttons are dead', async ({ page }) => {
  await page.goto('/runs');
  await expect(box(page).locator('button[data-step="full"]')).toBeDisabled();
  await expect(box(page)).toContainText('Sign in');
});
