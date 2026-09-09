import { expect, test } from '@playwright/test';

/**
 * Part 8 A and B: the Field shows a cost scatter only where cost exists, and
 * the media ranking where it does not.
 *
 * The scatter plots a quality axis against cost, so a point needs **both**.
 * When these were written, 5 of 313 scored media models carried a price and
 * every media tab fell back to the ranking.
 *
 * **Part 10 changed the data, not the rule.** Artificial Analysis prices media
 * on its leaderboards, the priced share went from 13.4% to 53.0%, and six of
 * the eight media modalities now clear `MIN_PRICED_SHARE` on their own. The
 * threshold was not touched. So these tests now assert the rule from both
 * sides: a modality that clears it gets its scatter back, and one that does not
 * still gets the ranking.
 */

test('llm keeps its scatter, because every model there has a price', async ({ page, request }) => {
  const board = await (await request.get('/v1/leaderboard?modality=llm')).json();
  expect(board.scatter_ok, 'llm should clear the priced threshold').toBe(true);

  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
});

test('a media modality below the threshold shows the ranking, not an empty scatter', async ({
  page,
  request
}) => {
  // music: its leaderboard publishes no price at all, so it is the one media
  // modality a marketplace is still the only answer for
  const board = await (await request.get('/v1/leaderboard?modality=music')).json();
  expect(board.scatter_ok, 'music is unpriced, so no cost axis').toBe(false);
  expect(board.rows.length).toBeGreaterThan(0);

  await page.goto('/field');
  await page.getByRole('tab', { name: /music/ }).click();

  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);

  // the leader from the API is the first row on screen
  await expect(page.locator('.bars li').first()).toContainText(board.rows[0].name);

  // and the axis picker is gone, since there is no scatter for it to steer
  await expect(page.locator('.picker')).toBeHidden();

  // it says why, rather than leaving a reader to wonder
  await expect(page.locator('.sub')).toContainText('cost cannot be plotted');
});

test('a media modality that now clears the threshold gets its scatter back', async ({
  page,
  request
}) => {
  // Part 10: Artificial Analysis prices media on its leaderboards. Nobody
  // lowered MIN_PRICED_SHARE -- the data crossed it.
  const board = await (await request.get('/v1/leaderboard?modality=text-to-video')).json();
  expect(board.scatter_ok, 'text-to-video is priced now').toBe(true);
  expect(board.priced / board.scored).toBeGreaterThan(0.25);

  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();

  await expect(page.locator('canvas')).toBeVisible();
  // and the ranking stays underneath: "which is best" is a different question
  // from "what does best cost"
  await expect(page.getByRole('heading', { name: /Ranked by/ })).toBeVisible();
});

test('the two searches work on a media scatter, without an effort line', async ({ page }) => {
  // part 8C's search was built on llm and is not llm-only. Media models publish
  // no effort modes, so the polyline simply has nothing to draw.
  await page.goto('/field');
  await page.getByRole('tab', { name: /text-to-video/ }).click();
  await expect(page.locator('canvas')).toBeVisible();

  const provider = page.getByLabel('Provider', { exact: true });
  await expect(provider).toBeVisible();
  const providers = page.locator('#field-providers option');
  expect(await providers.count()).toBeGreaterThan(1);

  const who = await providers.first().getAttribute('value');
  await provider.fill(who as string);
  await expect(page.locator('.count[role="status"]')).toContainText('lit');
});

test('the bar scale is the population and both endpoints are printed', async ({
  page,
  request
}) => {
  const board = await (await request.get('/v1/leaderboard?modality=music')).json();

  await page.goto('/field');
  await page.getByRole('tab', { name: /music/ }).click();
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
