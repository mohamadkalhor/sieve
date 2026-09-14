import { expect, test } from '@playwright/test';

test('guide renders the operating Markdown from the API', async ({ page }) => {
  await page.goto('/guide');
  await expect(page.getByRole('heading', { name: 'Operating Sieve as an agent' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Worked mutations' })).toBeVisible();
  await expect(page.locator('pre').first()).toContainText('profiles');
});

test('sources disables off-source pulls and saves and removes an alias', async ({ page }) => {
  await page.goto('/sources');
  const off = page.locator('article').filter({ has: page.locator('.name', { hasText: /^fal$/ }) });
  await expect(off.getByRole('button', { name: 'Pull now' })).toBeDisabled();
  await expect(off.getByRole('button')).toHaveAttribute('title', /disabled/);
  const row = page.locator('.unmatched li').filter({ has: page.locator('form') }).first();
  await expect(row).toBeVisible();
  const alias = await row.locator('.mono').textContent();
  await page.locator('input[type=password]').fill('ci-secret');
  const choice = await row.locator('select option').nth(1).getAttribute('value');
  await row.locator('select').selectOption(choice!);
  await row.getByRole('button', { name: 'Save', exact: true }).click();
  const saved = page.locator('.unmatched li').filter({ hasText: `${alias} →` });
  await expect(saved).toBeVisible();
  await saved.getByRole('button', { name: 'Remove' }).click();
  await expect(saved).toHaveCount(0);
  await expect(page.locator('.unmatched li').filter({ has: page.locator('form') }).filter({ hasText: alias! })).toBeVisible();
});
