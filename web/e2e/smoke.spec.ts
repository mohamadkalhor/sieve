import { expect, test, type Page } from '@playwright/test';

/**
 * One pass through the app against a real server, checking the things that
 * only break once both halves are wired together: a deep link, the slider
 * re-ranking locally, and the server's answer when a write has no token.
 *
 * Profiles, Rankings and Chains are one screen now, so these walk that screen
 * instead of three. The old URLs are still exercised, because they redirect
 * into it and a redirect that quietly stops working is exactly the kind of
 * thing nobody notices until a bookmark dies.
 */

/** the one row for a named profile, on the Profiles list */
const rowFor = (page: Page, profile: string) =>
  page.locator('li.row').filter({ has: page.locator(`#w-${profile}-cost`) });

test('a deep link opens that profile on the list', async ({ page }) => {
  await page.goto('/profiles/coder');
  await expect(page).toHaveURL(/\/profiles\?open=coder/);
  await expect(page.getByRole('heading', { name: 'Profiles', level: 1 })).toBeVisible();

  const row = rowFor(page, 'coder');
  await expect(row).toBeVisible();
  await expect(row.locator('ol.live li').first()).toBeVisible();
});

test('moving a weight re-ranks that row and says it is not applied', async ({ page }) => {
  await page.goto('/profiles');

  const row = rowFor(page, 'coder');
  const list = row.locator('ol.live li');
  // before anything is asked of the server the row shows what it is serving
  await expect(row.locator('.hintline')).toBeVisible();
  await expect(list.first()).toBeVisible();
  const before = await list.first().locator('.id').innerText();

  await expect(row.locator('.chip')).not.toContainText('changed');

  // cost to 0.95: the cheapest reachable model has to come out on top
  const cost = page.locator('#w-coder-cost');
  await cost.fill('0.95');
  await cost.dispatchEvent('input');

  await expect(row.locator('.chip')).toContainText('changed, not applied');
  await expect(row.locator('.hintline')).toHaveCount(0);
  await expect(list.first().locator('.id')).not.toHaveText(before);

  // and the weights still sum to 1
  await expect(row.locator('.sum')).toContainText('1.000');
});

test('a slider moves every row on its own, not the one next to it', async ({ page }) => {
  await page.goto('/profiles');

  const coder = rowFor(page, 'coder');
  await expect(coder.locator('ol.live li').first()).toBeVisible();

  const cost = page.locator('#w-coder-cost');
  await cost.fill('0.95');
  await cost.dispatchEvent('input');

  await expect(coder.locator('.chip')).toContainText('changed, not applied');
  // every other row is still showing what it shipped
  await expect(page.locator('li.row .chip', { hasText: 'changed, not applied' })).toHaveCount(1);
});

test('applying without a token shows the 401 rather than pretending', async ({ page }) => {
  await page.goto('/profiles');

  const row = rowFor(page, 'coder');
  const cost = page.locator('#w-coder-cost');
  await cost.fill('0.5');
  await cost.dispatchEvent('input');

  await row.getByRole('button', { name: 'Apply' }).click();
  await expect(row.locator('.error')).toContainText(/token/i);
});

test('discard puts the draft back', async ({ page }) => {
  await page.goto('/profiles');

  const row = rowFor(page, 'coder');
  const list = row.locator('ol.live li');
  await expect(list.first()).toBeVisible();

  // engage the row first, so what is compared is two previews and not a
  // preview against the chain the gateway happens to be holding
  const cost = page.locator('#w-coder-cost');
  await cost.fill('0.30');
  await cost.dispatchEvent('input');
  await expect(row.locator('.chip')).toContainText('changed, not applied');
  await row.getByRole('button', { name: 'Discard' }).click();
  await expect(row.locator('.chip')).not.toContainText('changed');

  const settled = await list.first().locator('.id').innerText();

  await cost.fill('0.95');
  await cost.dispatchEvent('input');
  await expect(row.locator('.chip')).toContainText('changed, not applied');

  await row.getByRole('button', { name: 'Discard' }).click();
  await expect(row.locator('.chip')).not.toContainText('changed');
  await expect(list.first().locator('.id')).toHaveText(settled);
});

test('the old Rankings URL opens the row, with what carried each score', async ({ page }) => {
  await page.goto('/rankings/coder');
  await expect(page).toHaveURL(/\/profiles\?open=coder/);

  const row = rowFor(page, 'coder');
  await expect(row.getByRole('heading', { name: 'What carried each score' })).toBeVisible();
  await expect(row.locator('tr.lead')).toBeVisible();
});

test('the old Chains URL lands on the list too', async ({ page }) => {
  await page.goto('/chains');
  await expect(page).toHaveURL(/\/profiles$/);
  await expect(page.getByRole('heading', { name: 'Profiles', level: 1 })).toBeVisible();
});

test('nothing scrolls sideways at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  for (const path of ['/field', '/profiles', '/profiles?open=coder', '/sources', '/connectors']) {
    await page.goto(path);
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth
    );
    expect(overflow, `${path} scrolls sideways at 390px`).toBe(false);
  }
});

test('the list still re-ranks with motion reduced', async ({ page }) => {
  // FLIP is a nicety; the reorder is the function. With reduce-motion on, the
  // animation has to collapse to nothing without taking the reorder with it.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/profiles');

  const list = rowFor(page, 'coder').locator('ol.live li');
  await expect(list.first()).toBeVisible();
  const before = await list.first().locator('.id').innerText();

  const cost = page.locator('#w-coder-cost');
  await cost.fill('0.95');
  await cost.dispatchEvent('input');

  await expect(list.first().locator('.id')).not.toHaveText(before);
});
