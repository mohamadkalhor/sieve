import { expect, test, type Page } from '@playwright/test';

/**
 * The addresses the console kept, and the three places it used to be quiet.
 *
 * The console moved `/profiles`, `/rankings` and `/chains` onto `/seats` and
 * left the old URLs in place, because they are in bookmarks, in the decision
 * log and in the guide's own links. A redirect is invisible while everything
 * else works, which is exactly why it needs a test: `/profiles` stopped
 * carrying `?open=<name>` when the list moved, and a redirect that lands on
 * nothing but a spinner looks the same as one that works until you click it.
 *
 * The rest of this file is the same complaint in three other places -- a failed
 * status read, a second request where one would do, a removed row with no way
 * back, and an "Unscored only" pill whose link did not move the browser. All
 * four were found by walking the built app by hand; all four are here so that
 * the next walk can start somewhere else.
 */

const TOKEN = 'ci-secret';
const SHIP_TABLE = { name: 'What these settings would ship' };
const POOL_TABLE = { name: 'Every reachable model' };

/** The `who` block of the top bar. Sign in after opening a page, never before. */
async function signIn(page: Page, token = TOKEN): Promise<void> {
  await page.locator('button.who').click();
  const field = page.getByPlaceholder('paste a bearer token');
  await field.fill(token);
  await page.keyboard.press('Escape');
  await expect(field).toHaveCount(0);
}

function shipRows(page: Page) {
  return page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
}

test('every address the console moved still lands on the seat it names', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  // A bare `/profiles` is the list, which at this width hands you the seat you
  // last worked -- so it may end on either, but it has to end on a screen.
  const list = /\/seats(\/[^/]+)?$/;
  const cases: { from: string; to: RegExp }[] = [
    { from: '/profiles', to: list },
    { from: '/profiles/coder', to: /\/seats\/coder$/ },
    { from: '/profiles?open=coder', to: /\/seats\/coder$/ },
    { from: '/rankings/coder', to: /\/seats\/coder$/ },
    { from: '/chains/coder', to: /\/seats\/coder$/ }
  ];

  for (const { from, to } of cases) {
    await page.goto(from);
    await expect(page, `${from} did not land on ${to}`).toHaveURL(to);
  }

  // and what the last redirect landed on is a screen, not a shell: the seat
  // answered and the table is there
  await expect(page.getByRole('heading', { level: 1 })).toContainText('coder');
  await expect(shipRows(page).first()).toBeVisible();
});

test('a status read that fails is said out loud in the bar', async ({ page }) => {
  // `bar()` draws a *reading*; a failed read has no reading to put in it, so a
  // 500 from `/v1/status` drew nothing at all and the bar said the round trip
  // was fine. The API answered here -- it is not unreachable -- it is wrong.
  await page.route('**/v1/status', (route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        error: { code: 'server', message: 'the store could not be read' }
      })
    })
  );
  await page.goto('/seats/coder');

  const bar = page.locator('footer.status');
  await expect(bar).toContainText('Cannot read the status');
  await expect(bar).toContainText('the store could not be read');
  // not "cannot reach the API": the API answered
  await expect(bar).not.toContainText('Cannot reach the API');
});

test('one request paints the seats pane', async ({ page }) => {
  // The seat page and the pane it draws both asked for the list. Two requests
  // where one would do is a second read of the same thing on every load, and
  // the store only shares a call that is still in flight, so the second one
  // was a real round trip.
  const calls: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/v1/seats') calls.push(request.url());
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats/coder');
  await expect(page.locator('aside[aria-label="Seats"] a[href="/seats/coder"]')).toHaveCount(1);
  await expect(shipRows(page).first()).toBeVisible();

  // a second read would have gone out with the first, not a second later
  await page.waitForTimeout(500);
  expect(calls, 'the seats list was asked for twice').toHaveLength(1);
});

test('a model taken off the lineup is under "Removed by you", and comes back', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats/coder');
  await expect(shipRows(page).first()).toBeVisible();
  await signIn(page);

  const table = page.getByRole('table', SHIP_TABLE);
  const row = shipRows(page).first();
  const remove = row.getByRole('button', { name: /^Never ship / });
  const label = ((await remove.getAttribute('aria-label')) ?? (await remove.innerText())).trim();
  const name = label.replace(/^Never ship\s*/, '').trim();
  expect(name, 'the remove button does not name its model').not.toBe('');

  await remove.click();

  // It is out of the lineup and under the divider that says who did it. The
  // divider is a section of the one table, not a second table, so the row is
  // still a row of this one -- the proof is the action: what was "Never ship"
  // is now "Restore", and there is nothing left to take off the list.
  await expect(table.getByRole('button', { name: `Never ship ${name}`, exact: true })).toHaveCount(0);
  await expect(table.getByRole('cell', { name: /Removed by you · 1/ })).toBeVisible();
  const restore = table.getByRole('button', { name: `Restore ${name}`, exact: true });
  await expect(restore).toBeVisible();

  await restore.click();

  // back among the rows that would ship, and the section is gone
  await expect(table.getByRole('button', { name: `Restore ${name}`, exact: true })).toHaveCount(0);
  await expect(table.getByRole('cell', { name: /Removed by you/ })).toHaveCount(0);
  await expect(table.getByRole('button', { name: `Never ship ${name}`, exact: true })).toBeVisible();
});

test('"Unscored only" leads to the screen that scores them', async ({ page }) => {
  // The pill filters the pool; the link beside it is the way to do something
  // about what is left, and it has to actually move the browser.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats/coder?view=all');
  await expect(page.getByRole('table', POOL_TABLE)).toBeVisible();

  await page.getByRole('button', { name: /^Unscored only/ }).click();

  // the link is the way to do something about what the pill leaves behind, and
  // it has to actually move the browser -- to a screen, not to a 404
  const link = page.getByRole('link', { name: 'score them' }).first();
  await expect(link, 'the fixture has nothing unscored to offer').toBeVisible();
  await link.click();

  await expect(page).toHaveURL(/\/unscored$/);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Unscored models');
});
