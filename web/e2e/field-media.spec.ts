import { expect, test } from '@playwright/test';

/**
 * Part 8 A and B: the Field shows a cost scatter only where cost exists, and
 * the media ranking where it does not.
 *
 * The scatter plots a quality axis against cost, so a point needs **both**.
 * Measured on the recordings: 5 of 313 scored media models carry a price,
 * against 60 of 60 LLMs. Five dots over an empty field read as "there are five
 * text-to-image models", which is false — the same failure the rest of this
 * system exists to avoid, an absence rendering as a fact.
 */

test('llm keeps its scatter, because every model there has a price', async ({ page, request }) => {
  const board = await (await request.get('/v1/leaderboard?modality=llm')).json();
  expect(board.scatter_ok, 'llm should clear the priced threshold').toBe(true);

  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
});

test('a media modality shows the ranking instead of an empty scatter', async ({
  page,
  request
}) => {
  const board = await (await request.get('/v1/leaderboard?modality=text-to-video')).json();
  expect(board.scatter_ok, 'media should not clear the threshold on this data').toBe(false);
  expect(board.rows.length).toBeGreaterThan(0);

  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();

  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);

  // the leader from the API is the first row on screen
  await expect(page.locator('.bars li').first()).toContainText(board.rows[0].name);

  // and the axis picker is gone, since there is no scatter for it to steer
  await expect(page.locator('.picker')).toBeHidden();
});

test('the bar scale is the population and both endpoints are printed', async ({
  page,
  request
}) => {
  const board = await (await request.get('/v1/leaderboard?modality=text-to-video')).json();

  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();
  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();

  const scale = await page.locator('.scale').innerText();
  expect(scale).toContain(String(Math.round(board.low)));
  expect(scale).toContain(String(Math.round(board.high)));
});

test('twelve rows by default, with a control for the rest', async ({ page }) => {
  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();
  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();

  await expect(page.locator('.bars li')).toHaveCount(12);
  const more = page.getByRole('button', { name: /Show all/ });
  await expect(more).toBeVisible();
  await more.click();
  expect(await page.locator('.bars li').count()).toBeGreaterThan(12);
});

test('speech-to-text says why there is no ranking rather than drawing ties', async ({
  page,
  request
}) => {
  const board = await (await request.get('/v1/leaderboard?modality=speech-to-text')).json();
  expect(board.rows).toHaveLength(0);
  expect(board.reason).toContain('rounded to one decimal');

  await page.goto('/field');
  await page.getByRole('tab', { name: /speech-to-text/ }).click();

  await expect(page.getByRole('heading', { name: /No ranking for speech-to-text/ })).toBeVisible();
  await expect(page.locator('.bars')).toHaveCount(0);
  await expect(page.locator('canvas')).toHaveCount(0);
});

test('music offers both of its two leaderboards', async ({ page, request }) => {
  const board = await (await request.get('/v1/leaderboard?modality=music')).json();
  expect(board.metrics).toEqual(['elo:with_vocals', 'elo:instrumental']);

  await page.goto('/field');
  await page.getByRole('tab', { name: /music/ }).click();
  await expect(page.getByRole('heading', { name: /Ranked by with vocals/ })).toBeVisible();

  await page.locator('.metric select').selectOption('elo:instrumental');
  await expect(page.getByRole('heading', { name: /Ranked by instrumental/ })).toBeVisible();
});

test('duplicates published under two names are collapsed', async ({ request }) => {
  const board = await (await request.get('/v1/leaderboard?modality=text-to-video')).json();
  const names: string[] = board.rows.map((r: { name: string }) => r.name);
  expect(new Set(names).size, 'no name appears twice').toBe(names.length);

  // and at least one row actually absorbed another, or the dedupe is untested
  const merged = board.rows.filter((r: { merged: string[] }) => r.merged.length > 0);
  expect(merged.length, 'AA publishes the same model under several ids').toBeGreaterThan(0);
});

test('no horizontal scroll at 390px on a media ranking', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();
  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(overflow).toBe(false);
});
