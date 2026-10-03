import { expect, test, type Page } from '@playwright/test';

/**
 * Runs at, end to end (EFFORT.md section 7), on the seeded store.
 *
 * The seed's gateway reaches GPT-5.6 Luna bare -- so under "any" it is scored
 * at its top effort, max -- and by `openai/gpt-5-6-luna-xhigh`, an id that names
 * its own mode. Setting the coder seat to medium has to move the bare row's
 * score to the medium row (its pill says so), leave the id-named row alone,
 * count as a change on Ship, and mark the medium rung in the inspector's
 * ladder. The seat is put back to any at the end: the store is shared by
 * every spec in the run.
 */

const SEAT = 'coder';
const BARE = 'openai/gpt-5-6-luna';
const XHIGH = 'openai/gpt-5-6-luna-xhigh';
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

function row(page: Page, id: string) {
  return page.getByRole('table', POOL_TABLE).locator(`[role="row"][data-row="${id}"]`);
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

  // under any, the bare id is scored at the row it matched: the top effort
  const bare = row(page, BARE);
  await expect(bare.locator('[data-effort-pill]')).toHaveText('max');
  await expect(bare.locator('[data-effort-pill]')).toHaveAttribute('data-look', 'any');
  const before = await bare.locator('.figure').innerText();

  await runsAt(page, 'medium');

  await expect(bare.locator('[data-effort-pill]')).toHaveText('medium');
  await expect(bare.locator('[data-effort-pill]')).toHaveAttribute('data-look', 'exact');
  // medium Luna is credited with less than max Luna
  await expect(bare.locator('.figure')).not.toHaveText(before);
  expect(Number(await bare.locator('.figure').innerText())).toBeLessThan(Number(before));
  // the router id names its own mode, and the seat does not override it
  await expect(row(page, XHIGH).locator('[data-effort-pill]')).toHaveText('xhigh · id');

  await expect(page.locator('[data-effort-hint]')).toContainText(
    "Was any: scored at each model's top effort."
  );
  // a new effort is a change even when no router id moves
  const ship = page.getByRole('button', { name: /^Ship \d+ changes?$/ });
  await expect(ship).toBeEnabled();

  // the inspector opens on the ladder, the medium rung marked
  await bare.click();
  const ladder = page.locator('[data-block="ladder"]');
  await expect(ladder).toBeVisible();
  await expect(ladder.locator('[data-here="true"]')).toHaveAttribute('data-effort', 'medium');
  await expect(ladder).toContainText('The seat runs at medium, and');

  // it is a saved setting: a reload opens at medium
  await expect
    .poll(async () => (await (await page.request.get(`/v1/profiles/${SEAT}/settings`)).json()).effort)
    .toBe('medium');
  await page.reload();
  // the pasted token lives in memory, so a reload is signed out again
  await signIn(page);
  await expect(
    page.getByRole('radiogroup', { name: 'Effort this seat runs at' }).getByRole('radio', {
      name: 'medium',
      exact: true
    })
  ).toHaveAttribute('aria-checked', 'true');

  await runsAt(page, 'any');
  await expect(row(page, BARE).locator('[data-effort-pill]')).toHaveText('max');

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
