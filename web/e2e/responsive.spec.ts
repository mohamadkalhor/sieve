import { expect, test, type Page } from '@playwright/test';

/**
 * Section 6.8's four layouts, at the three widths that decide between them.
 *
 * 1280 is where three panes fit without the table losing the columns it has on
 * a wide screen; 1024 is where the inspector has to stop taking a column of its
 * own; 900 is where the seats pane stops being a pane and becomes a switcher in
 * the seat's header. The boundaries are the whole point of this file: an
 * off-by-one at 1024 puts the seats pane and the switcher on screen together,
 * and one at 900 puts a column and a list on screen together.
 *
 * The media queries in the components and `layout/viewport.svelte.ts` are two
 * statements of the same three numbers, and nothing in either can see the
 * other. This is where they are held together.
 */

const SHIP_TABLE = { name: 'What these settings would ship' };
const POOL_TABLE = { name: 'Every reachable model' };

/** The seat `/seats` opens, with the window set to a known width first. */
async function openSeat(page: Page, width = 1280): Promise<string> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/seats');
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);
  const seat = new URL(page.url()).pathname.split('/').pop() ?? '';
  expect(seat, 'no seat to open').not.toBe('');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  return seat;
}

/** Does the page scroll sideways? `+1` for the sub-pixel a border can round to. */
async function sideways(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

test('no route scrolls sideways, at any of the widths', async ({ page }) => {
  // the three widths section 6.8 names, over every route: thirty navigations,
  // which is why this one is marked slow rather than trimmed
  test.slow();
  const seat = await openSeat(page);

  const routes = [
    '/seats',
    `/seats/${seat}`,
    `/seats/${seat}?view=all`,
    '/field',
    '/sources',
    '/connectors',
    '/runs',
    '/unscored',
    '/guide',
    '/rankings/coder'
  ];

  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await expect
        .poll(async () => sideways(page), { message: `${route} scrolls sideways at ${width}px` })
        .toBeLessThanOrEqual(1);
    }
  }
});

test('the table keeps the page from scrolling sideways while it is loaded', async ({ page }) => {
  const seat = await openSeat(page);
  for (const width of [375, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/seats/${seat}?view=all`);
    await expect(page.getByRole('table', POOL_TABLE)).toBeVisible();
    await expect.poll(async () => sideways(page)).toBeLessThanOrEqual(1);
  }
});

test('three panes are three columns from 1280 up', async ({ page }) => {
  const seat = await openSeat(page, 1280);

  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/seats/${seat}`);

    const list = page.locator('[data-slot="seats"]');
    const middle = page.locator('[data-slot="seat"]');
    const inspect = page.locator('[data-slot="inspector"]');
    for (const pane of [list, middle, inspect]) await expect(pane).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    // left to right, and none of them on top of another
    const [a, b, c] = [
      await list.boundingBox(),
      await middle.boundingBox(),
      await inspect.boundingBox()
    ];
    expect(a && b && c, 'a pane has no box').toBeTruthy();
    if (!a || !b || !c) return;
    expect(a.x + a.width).toBeLessThanOrEqual(b.x + 1);
    expect(b.x + b.width).toBeLessThanOrEqual(c.x + 1);

    // the wide table has its columns
    await expect(
      page.getByRole('table', SHIP_TABLE).getByRole('columnheader', { name: 'Per task', exact: true })
    ).toBeVisible();
  }
});

test('below 1280 the inspector is an overlay, not a column', async ({ page }) => {
  const seat = await openSeat(page);

  for (const width of [1024, 1100, 1279]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/seats/${seat}`);

    // the seats pane is still a pane, the inspector is not
    await expect(page.locator('[data-slot="seats"]')).toBeVisible();
    await expect(page.locator('[data-slot="inspector"]')).toBeHidden();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    const rows = page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
    await rows.nth(1).click();

    const drawer = page.getByRole('dialog', { name: 'Inspector' });
    await expect(drawer).toBeVisible();
    // a drawer, not a sheet: it is up against the seat pane's right edge
    const seatBox = await page.locator('[data-slot="seat"]').boundingBox();
    const box = await drawer.boundingBox();
    expect(seatBox && box).toBeTruthy();
    if (!seatBox || !box) return;
    expect(box.x).toBeGreaterThan(seatBox.x + 40);
    expect(Math.abs(box.x + box.width - (seatBox.x + seatBox.width))).toBeLessThanOrEqual(1);

    // and it is the inspector, once: the column is not drawn behind it
    await expect(page.locator('[data-slot="inspector"]')).toBeHidden();
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
  }
});

test('from 1023 down the seats pane is the switcher instead', async ({ page }) => {
  const seat = await openSeat(page);

  for (const width of [900, 1000, 1023]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/seats/${seat}`);

    await expect(page.locator('[data-slot="seats"]')).toBeHidden();
    const switcher = page.getByRole('button', { name: new RegExp(seat) }).first();
    await expect(switcher).toBeVisible();

    await switcher.click();
    const sheet = page.getByRole('dialog', { name: 'Seats' });
    await expect(sheet).toBeVisible();
    await expect(sheet.locator('a[href^="/seats/"]').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
  }

  // and at 1024 the pane is a pane again, with no switcher beside it
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto(`/seats/${seat}`);
  await expect(page.locator('[data-slot="seats"]')).toBeVisible();
  await expect(page.getByRole('button', { name: new RegExp(seat) })).toHaveCount(0);
});

test('below 900 one column, and the list is its own screen', async ({ page }) => {
  const seat = await openSeat(page);

  for (const width of [375, 768, 899]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/seats/${seat}`);

    await expect(page.locator('[data-slot="seats"]')).toBeHidden();
    await expect(page.locator('[data-slot="inspector"]')).toBeHidden();
    await expect(page.locator('[data-slot="seat"]')).toBeVisible();

    // the top bar is the compact one: a search button and the menu, no links
    await expect(page.getByRole('navigation', { name: 'Sections' })).toBeHidden();
    await expect(page.getByRole('button', { name: 'Sections' })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Find a model, a seat or an action' })
    ).toBeVisible();

    // `/seats` is the list here, not a redirect
    await page.goto('/seats');
    await expect(page).toHaveURL(/\/seats$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Seats' })).toBeVisible();
    await expect(page.locator('aside[aria-label="Seats"] a[href^="/seats/"]').first()).toBeVisible();
  }
});

test('the table drops three columns below 900 and keeps its three numbers', async ({ page }) => {
  const seat = await openSeat(page);

  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto(`/seats/${seat}`);
  const table = page.getByRole('table', SHIP_TABLE);
  for (const column of ['Model', 'Score', 'Per task', 'Can do', 'Via']) {
    await expect(table.getByRole('columnheader', { name: column, exact: true })).toBeVisible();
  }

  await page.setViewportSize({ width: 768, height: 900 });
  await page.goto(`/seats/${seat}`);
  const narrow = page.getByRole('table', SHIP_TABLE);
  await expect(narrow.getByRole('columnheader', { name: 'Model', exact: true })).toBeVisible();
  await expect(narrow.getByRole('columnheader', { name: 'Score', exact: true })).toBeVisible();
  for (const dropped of ['Per task', 'Can do', 'Via']) {
    await expect(narrow.getByRole('columnheader', { name: dropped, exact: true })).toBeHidden();
  }

  // a row draws the same three cells, and the buttons under the name still fit
  const row = narrow.locator('[role="row"][data-row]').first();
  await expect(row.locator('.cell.model .name')).toBeVisible();
  await expect(row.locator('.cell.rank')).toBeVisible();
  await expect(row.locator('.cell.score')).toBeVisible();
  await expect(row.locator('.cell.task')).toBeHidden();
});

test('below 900 the inspector is a sheet, and nothing on it is a 30px target', async ({ page }) => {
  const seat = await openSeat(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto(`/seats/${seat}`);

  const rows = page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
  await rows.first().click();

  const sheet = page.getByRole('dialog', { name: 'Inspector' });
  await expect(sheet).toBeVisible();
  const seatBox = await page.locator('[data-slot="seat"]').boundingBox();
  const box = await sheet.boundingBox();
  expect(seatBox && box).toBeTruthy();
  if (!seatBox || !box) return;
  // a sheet sits at the bottom and spans the width
  expect(box.width).toBeGreaterThan(seatBox.width - 2);
  expect(box.y + box.height).toBeGreaterThanOrEqual(seatBox.y + seatBox.height - 2);

  // §6.8: every target on a phone is 44px or more
  for (const target of [
    page.getByRole('button', { name: 'Sections' }),
    page.getByRole('button', { name: 'Find a model, a seat or an action' }),
    page.getByRole('button', { name: /^Ship/ })
  ]) {
    const size = await target.boundingBox();
    expect(size).toBeTruthy();
    if (!size) return;
    expect(Math.round(size.height), `${await target.getAttribute('aria-label')} is short`).toBeGreaterThanOrEqual(44);
    expect(Math.round(size.width)).toBeGreaterThanOrEqual(44);
  }

  await expect(sheet.locator('button').first()).toBeVisible();
  const first = await sheet.locator('button').first().boundingBox();
  if (first) expect(Math.round(first.height)).toBeGreaterThanOrEqual(44);
});
