import { expect, test, type Page } from '@playwright/test';

/**
 * Runs at, end to end (EFFORT.md section 7), on the seeded store.
 *
 * The seed's gateway reaches `openai/gpt-oss-20b` bare, and AA publishes that
 * family at two efforts, its top one and low -- no medium. Under "any" the
 * bare id is scored at the row it matched; setting the coder seat to medium
 * has to score it at low instead, the nearest effort below (the dashed
 * stand-in pill), count as a change on Ship, and mark the low rung in the
 * inspector's ladder with the unpublished medium rung greyed above it. The
 * seat is put back to any at the end: the store is shared by every spec in
 * the run. The exact and id-named looks are pinned in vitest
 * (`tests/console/h-effort.test.ts`).
 */

const SEAT = 'coder';
const BARE = 'openai/gpt-oss-20b';
const POOL_TABLE = { name: 'Every reachable model' };
/** The seeded store's write token: Runs at is a setting, and a setting is saved. */
const TOKEN = 'ci-secret';

/** The `who` block of the top bar. Sign in after opening a page, never before. */
async function signIn(page: Page): Promise<void> {
  await page.locator('button.who').click();
  const field = page.getByPlaceholder('paste a bearer token');
  await field.fill(TOKEN);
  await page.keyboard.press('Escape');
  await expect(field).toHaveCount(0);
}

function pill(page: Page, id: string) {
  return page
    .getByRole('table', POOL_TABLE)
    .locator(`[role="row"][data-row="${id}"]`)
    .locator('[data-effort-pill]');
}

async function runsAt(page: Page, label: string): Promise<void> {
  await page
    .getByRole('radiogroup', { name: 'Effort this seat runs at' })
    .getByRole('radio', { name: label, exact: true })
    .click();
}

test('Runs at re-scores the seat at that effort and Ship counts it', async ({ page }) => {
  const thrown: string[] = [];
  page.on('pageerror', (error) => thrown.push(String(error)));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/seats/${SEAT}?view=all`);
  await signIn(page);

  const control = page.locator('[data-block="runs-at"]');
  await expect(control).toBeVisible();
  await expect(control.getByRole('radio', { name: 'any', exact: true })).toHaveAttribute(
    'aria-checked',
    'true'
  );

  // under any, the bare id is scored at the row it matched
  await expect(pill(page, BARE)).toHaveAttribute('data-look', 'any');
  const top = await pill(page, BARE).innerText();
  expect(top).not.toBe('low');

  await runsAt(page, 'medium');

  // no medium row is published: a stand-in, from below, and it says so
  await expect(pill(page, BARE)).toHaveText('low ↓');
  await expect(pill(page, BARE)).toHaveAttribute('data-look', 'near');
  await expect(pill(page, BARE)).toHaveAttribute('title', /^No medium row is published/);

  await expect(page.locator('[data-effort-hint]')).toContainText(
    "Was any: scored at each model's top effort."
  );
  // a new effort is a change even when no router id moves
  await expect(page.getByRole('button', { name: /^Ship \d+ changes?$/ })).toBeEnabled();

  // the inspector opens on the ladder, the low rung marked, medium greyed
  await page.getByRole('table', POOL_TABLE).locator(`[role="row"][data-row="${BARE}"]`).click();
  const ladder = page.locator('[data-block="ladder"]');
  await expect(ladder).toBeVisible();
  await expect(ladder.locator('[data-here="true"]')).toHaveAttribute('data-effort', 'low');
  await expect(ladder.locator('[data-effort="medium"]')).toHaveAttribute('data-published', 'false');
  await expect(ladder.locator('[data-effort="medium"]')).toContainText('seat runs here');
  await expect(ladder).toContainText('so it is scored at low, the nearest below');

  // it is a saved setting: a reload opens at medium
  await expect
    .poll(async () => (await (await page.request.get(`/v1/profiles/${SEAT}/settings`)).json()).effort)
    .toBe('medium');
  await page.reload();
  // the pasted token lives in memory, so a reload is signed out again
  await signIn(page);
  await expect(control.getByRole('radio', { name: 'medium', exact: true })).toHaveAttribute(
    'aria-checked',
    'true'
  );

  await runsAt(page, 'any');
  await expect(pill(page, BARE)).toHaveText(top);
  await expect
    .poll(async () => (await (await page.request.get(`/v1/profiles/${SEAT}/settings`)).json()).effort)
    .toBeNull();

  expect(thrown, 'the seat page threw').toEqual([]);
});

test('a phone gets one 44px Runs at row that opens a sheet', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/seats/${SEAT}`);

  const open = page.locator('[data-block="runs-at"] button[aria-haspopup="dialog"]');
  await expect(open).toBeVisible();
  const box = await open.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(375);

  await open.click();
  const sheet = page.getByRole('dialog', { name: 'Effort this seat runs at' });
  await expect(sheet.getByRole('radio', { name: /^minimal/ })).toBeVisible();
  for (const choice of await sheet.getByRole('radio').all()) {
    expect((await choice.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  await page.keyboard.press('Escape');

  // nothing runs past the right edge
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(wide).toBeLessThanOrEqual(375);
});
