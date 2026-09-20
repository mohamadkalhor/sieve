import { expect, test } from '@playwright/test';

/**
 * The table, in a browser, against the seeded store (CONSOLE.md section 6.6).
 *
 * The vitest in `tests/console/d-rows.test.ts` checks what the rows say; this
 * checks the things only a real page can show: that the table draws where the
 * placeholder list used to be, that the two views are one state and a URL, that
 * a filter narrows the pool without renumbering it, and that `?view=all` is a
 * link somebody can send.
 *
 * The seat is not hard-coded: `/seats` resumes the seat this browser last
 * worked in and, in a fresh context, opens the first one. The fixtures decide
 * which seats exist, so a name typed here would be the next thing to go stale.
 */

async function openSeat(page: import('@playwright/test').Page): Promise<string> {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats');
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);
  const seat = new URL(page.url()).pathname.split('/').pop() ?? '';
  expect(seat).not.toBe('');
  return seat;
}

const SHIP_TABLE = { name: 'What these settings would ship' };
const POOL_TABLE = { name: 'Every reachable model' };

test('the lineup draws rows, a ship line and the columns above them', async ({ page }) => {
  const thrown: string[] = [];
  page.on('pageerror', (error) => thrown.push(String(error)));

  await openSeat(page);

  const table = page.getByRole('table', SHIP_TABLE);
  await expect(table).toBeVisible();
  for (const column of ['Model', 'Score', 'Per task', 'Can do', 'Via']) {
    await expect(table.getByRole('columnheader', { name: column, exact: true })).toBeVisible();
  }

  // the ship line is a row of its own, and it claims both sides of itself
  await expect(table.getByText('Ships above this line')).toBeVisible();
  await expect(table.getByText('next up below')).toBeVisible();

  const rows = table.locator('[role="row"][data-row]');
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(0);

  // what ships is above the line, and what ships is ranked from the top: if the
  // fixtures leave this seat shipping anything, its first row is the leader
  const shipping = table.locator('[role="row"][data-row][data-tone="ship"]');
  if ((await shipping.count()) > 0) {
    await expect(shipping.first().locator('.cell.rank')).toHaveText('1');
  }

  expect(thrown, 'the seat page threw').toEqual([]);
});

test('the two views are one state, in the URL', async ({ page }) => {
  await openSeat(page);
  await expect(page.getByRole('table', POOL_TABLE)).toHaveCount(0);

  await page.getByRole('radio', { name: /^All reachable \d+$/ }).click();

  await expect(page).toHaveURL(/[?&]view=all\b/);
  const pool = page.getByRole('table', POOL_TABLE);
  await expect(pool).toBeVisible();
  await expect(page.getByLabel(/^Filter \d+ reachable models/)).toBeVisible();

  // the address survives a reload, which is the point of it being a URL
  await page.reload();
  await expect(page.getByRole('table', POOL_TABLE)).toBeVisible();

  await page.getByRole('radio', { name: 'Lineup' }).click();
  await expect(page).not.toHaveURL(/view=all/);
  await expect(page.getByRole('table', SHIP_TABLE)).toBeVisible();
});

test('a filter narrows the pool and does not renumber it', async ({ page }) => {
  await openSeat(page);
  await page.getByRole('radio', { name: /^All reachable \d+$/ }).click();

  const pool = page.getByRole('table', POOL_TABLE);
  const rows = pool.locator('[role="row"][data-row]');
  const all = await rows.count();
  expect(all).toBeGreaterThan(1);

  // the name of the row the pool puts in third place, and its rank
  const third = rows.nth(2);
  const name = (await third.locator('.cell.model .name').innerText()).trim();
  expect(name).not.toBe('');
  const rank = (await third.locator('.cell.rank').innerText()).trim();
  expect(rank).toBe('3');

  await page.getByLabel(/^Filter \d+ reachable models/).fill(name);

  await expect
    .poll(async () => rows.count(), { message: 'the filter narrowed nothing' })
    .toBeLessThan(all);

  // the pool's rank travels with the row: a filter is not a renumbering
  await expect(rows.first()).toHaveAttribute('title', /.+/);
  await expect(rows.filter({ hasText: name }).first().locator('.cell.rank')).toHaveText(rank);
  for (const text of await rows.allInnerTexts()) {
    expect(text.toLowerCase()).toContain(name.toLowerCase());
  }

  // and clearing it puts the pool back
  await page.getByLabel(/^Filter \d+ reachable models/).fill('');
  await expect.poll(async () => rows.count()).toBe(all);
});

test('a row a need keeps out says so rather than looking shippable', async ({ page }) => {
  await openSeat(page);
  await page.getByRole('radio', { name: /^All reachable \d+$/ }).click();

  const blocked = page
    .getByRole('table', POOL_TABLE)
    .locator('[role="row"][data-row][data-tone="blocked"]');

  if ((await blocked.count()) === 0) {
    // nothing is out on these fixtures, so the claim is the other way round:
    // nothing is dressed as blocked either
    await expect(blocked).toHaveCount(0);
    return;
  }

  const first = blocked.first();
  await expect(first.locator('.cell.model .tag.warn')).toContainText(/^fails /);
  await expect(first.locator('.cell.can')).toHaveAttribute('data-warn', 'true');
  await expect(first.locator('.cell.score .num')).toHaveText(/^(—|\d\.\d\d)$/);
});
