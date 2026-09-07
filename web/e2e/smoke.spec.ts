import { expect, test } from '@playwright/test';

/**
 * One pass through the app against a real server, checking the things that
 * only break once both halves are wired together: a deep link, the slider
 * re-ranking locally, and the server's answer when a write has no token.
 */

test('a deep link renders the profile editor', async ({ page }) => {
  await page.goto('/profiles/coder');
  await expect(page.getByRole('heading', { name: 'coder', level: 1 })).toBeVisible();
  await expect(page.getByText('Live ranking')).toBeVisible();
});

test('moving a weight re-ranks the list and marks it unsaved', async ({ page }) => {
  await page.goto('/profiles/coder');

  const list = page.locator('.live li');
  await expect(list.first()).toBeVisible();
  const before = await list.first().locator('.id').innerText();

  await expect(page.locator('.unsaved')).toHaveCount(0);

  // cost to 0.95: the cheapest reachable model has to come out on top
  const cost = page.locator('#w-cost');
  await cost.fill('0.95');
  await cost.dispatchEvent('input');

  await expect(page.locator('.unsaved')).toBeVisible();
  const after = await list.first().locator('.id').innerText();
  expect(after).not.toBe(before);

  // and the weights still sum to 1
  await expect(page.locator('.sum')).toContainText('1.000');
});

test('saving without a token shows the 401 rather than pretending', async ({ page }) => {
  await page.goto('/profiles/coder');

  const cost = page.locator('#w-cost');
  await cost.fill('0.5');
  await cost.dispatchEvent('input');

  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.error')).toContainText(/token/i);
});

test('evaluate asks the server and shows what it decided', async ({ page }) => {
  await page.goto('/profiles/coder');
  await page.getByRole('button', { name: 'Evaluate' }).click();
  await expect(page.locator('.notice, .error').first()).toBeVisible();
});

test('the rankings screen explains why the leader leads', async ({ page }) => {
  await page.goto('/rankings/coder');
  await expect(page.locator('tr.lead')).toBeVisible();
  await expect(page.locator('.why')).toContainText('leads');
});

test('nothing scrolls sideways at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  for (const path of ['/field', '/rankings/coder', '/profiles/coder', '/chains', '/sources']) {
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
  await page.goto('/profiles/coder');

  const list = page.locator('.live li');
  await expect(list.first()).toBeVisible();
  const before = await list.first().locator('.id').innerText();

  const cost = page.locator('#w-cost');
  await cost.fill('0.95');
  await cost.dispatchEvent('input');

  await expect(list.first().locator('.id')).not.toHaveText(before);
});
