import { expect, test, type Page } from '@playwright/test';

/**
 * A seat, on its own page (CONSOLE.md section 1.2), and the five things the
 * first version of this file was about -- a link that carries the seat, a
 * change that is a draft, two seats that do not touch, a refused token that is
 * said out loud, and a discard.
 *
 * The old file opened `/profiles/coder` and dragged a weight slider. There is
 * no such page now and nothing in this app ranks a list from a slider: the
 * section's own acceptance list says `/seats/coder` and a keyboard drag, so
 * this file is the same five intents with the page they are about.
 *
 * Two things it learned the hard way and now states:
 *
 * - Reads are open; writes are not. Without a token the seat answers, renders
 *   and previews, and the 400ms-save that follows any edit comes back 401 and
 *   `api/client.ts` sends the browser to the login route -- off this page and
 *   onto one the console does not serve. So the tests that edit sign in first.
 * - On this fleet's fixtures a weight drag moves the bar and nothing else: the
 *   top three of a seat's pool do not reorder under +5% on one axis (measured
 *   -- see `rerank.perf.spec.ts`), so `session.shipText` stays "Ship now" and
 *   a drag would prove nothing. A change that really re-ranks the list is the
 *   one the rows own: taking a model off what ships.
 */

const TOKEN = 'ci-secret';
const SHIP_TABLE = { name: 'What these settings would ship' };
const POOL_TABLE = { name: 'Every reachable model' };
const TOAST = '.toast';

/** The `who` block of the top bar: the pasted token is what the writes carry. */
async function signIn(page: Page, token = TOKEN): Promise<void> {
  await page.locator('button.who').click();
  const field = page.getByPlaceholder('paste a bearer token');
  await field.fill(token);
  await page.keyboard.press('Escape');
  await expect(field).toHaveCount(0);
}

/** `/seats` hands you the seat you last worked, and the page has answered. */
async function open(page: Page, path = '/seats'): Promise<string> {
  await page.goto(path);
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);
  const seat = decodeURIComponent(new URL(page.url()).pathname.split('/').pop() ?? '');
  expect(seat, 'no seat to open').not.toBe('');
  await expect(page.getByRole('heading', { level: 1 })).toContainText(seat);
  await expect(shipRows(page).first()).toBeVisible();
  return seat;
}

function shipRows(page: Page) {
  return page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
}

function listRows(page: Page) {
  return page.getByRole('table', POOL_TABLE).locator('[role="row"][data-row]');
}

/** The ship button is the one whose text is `session.shipText`. */
function ship(page: Page) {
  return page.getByRole('button', { name: /^Ship/ });
}

/** What the seat will tell the next reader it last shipped. */
async function applied(page: Page, seat: string): Promise<string> {
  const link = page.locator(`aside[aria-label="Seats"] a[href="/seats/${seat}"]`);
  await expect(link).toHaveCount(1);
  return (await link.innerText()).replace(/\s+/g, ' ').trim();
}

/** The bar's segments, which are the weights as the page draws them. */
async function weights(page: Page): Promise<string[]> {
  return page.locator('.track button.seg').evaluateAll((els) =>
    els.map((el) => el.getAttribute('aria-label') ?? '')
  );
}

/** Take the top of what ships off the list: the draft re-ranks around it. */
async function rerank(page: Page): Promise<string> {
  const row = shipRows(page).first();
  const before = await row.getAttribute('data-row');
  await row.getByRole('button', { name: /^(Never ship|Take off)/ }).click();
  await expect(ship(page)).toHaveText(/^Ship \d+ changes?$/);
  return before ?? '';
}

test('a seat is a page: the url carries it, and the panes around it are there', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await open(page);

  // the three panes of section 6.3-6.6, one beside the other at 1280
  await expect(page.locator('[data-slot="seats"]')).toBeVisible();
  await expect(page.locator('[data-slot="seat"]')).toBeVisible();
  await expect(page.locator('[data-slot="inspector"]')).toBeVisible();
  await expect(page.locator('[data-slot="seats"] a[aria-current="page"]')).toBeVisible();

  // the weights bar, and the columns the table has when there is room
  await expect(page.locator('.track button.seg').first()).toBeVisible();
  const table = page.getByRole('table', SHIP_TABLE);
  for (const column of ['Model', 'Score', 'Per task', 'Can do', 'Via']) {
    await expect(table.getByRole('columnheader', { name: column, exact: true })).toBeVisible();
  }

  // a row opens in the inspector, and the link carries the seat on its own
  const first = shipRows(page).first();
  const id = await first.getAttribute('data-row');
  await first.click();
  await expect(page.locator('[data-slot="inspector"]')).toContainText(id ?? '');
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);

  await page.goto('/seats/coder');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('coder');
  await expect(shipRows(page).first()).toBeVisible();

  // the pool behind it: every reachable model, and the id of the seat in the url
  await page.goto('/seats/coder?view=all');
  await expect(page.getByRole('table', POOL_TABLE)).toBeVisible();
  await expect(listRows(page).first()).toBeVisible();
});

test('a re-rank is a draft until it is shipped', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);
  const seat = await open(page);
  const chain = await applied(page, seat);
  const rows = await shipRows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-row'))
  );
  await expect(ship(page)).toBeDisabled();

  await rerank(page);
  const after = await shipRows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-row'))
  );
  expect(after, 'the list did not re-rank').not.toEqual(rows);

  // the seat still says it last shipped what it shipped, and a reload does not
  // apply the draft behind the reader's back
  expect(await applied(page, seat)).toBe(chain);
  await page.reload();
  await expect(shipRows(page).first()).toBeVisible();
  await expect(ship(page)).toHaveText(/^Ship \d+ changes?$/);
  expect(await applied(page, seat)).toBe(chain);

  // and the seat is still the one the url names
  await expect(page.getByRole('heading', { level: 1 })).toContainText(seat);
});

test('working one seat leaves the seat next door alone', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);
  const here = await open(page);

  const other = await page
    .locator('aside[aria-label="Seats"] a[href^="/seats/"]')
    .evaluateAll((els) =>
      els
        .map((el) => el.getAttribute('href') ?? '')
        .find((href) => href !== `/seats/${here}`) ?? ''
    );
  expect(other, 'the pane lists no other seat').not.toBe('');

  await page.goto(other);
  await expect(shipRows(page).first()).toBeVisible();
  const before = await shipRows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-row'))
  );
  const barBefore = await weights(page);

  await page.goto(`/seats/${here}`);
  await expect(shipRows(page).first()).toBeVisible();
  await rerank(page);

  await page.goto(other);
  await expect(shipRows(page).first()).toBeVisible();
  expect(await weights(page)).toEqual(barBefore);
  expect(
    await shipRows(page).evaluateAll((els) => els.map((el) => el.getAttribute('data-row')))
  ).toEqual(before);
});

test('a token gate refuses is said out loud, and the change does not go out', async ({
  page
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);
  const seat = await open(page);
  const chain = await applied(page, seat);
  await rerank(page);

  // a token that is not one of the server's: the ship is refused, not sailed
  await signIn(page, 'not-a-token-gate-knows');
  await ship(page).click();

  await expect(page.locator(TOAST)).toContainText(/token/i);
  await expect(ship(page)).toHaveText(/^Ship \d+ changes?$/);
  expect(await applied(page, seat)).toBe(chain);
});

test('discard puts the settings back', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);
  await open(page);
  const bar = await weights(page);
  const rows = await shipRows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute('data-row'))
  );

  await rerank(page);
  await page.getByRole('button', { name: 'Reset', exact: true }).click();

  await expect(ship(page)).toBeDisabled();
  expect(await weights(page)).toEqual(bar);
  expect(
    await shipRows(page).evaluateAll((els) => els.map((el) => el.getAttribute('data-row')))
  ).toEqual(rows);
});
