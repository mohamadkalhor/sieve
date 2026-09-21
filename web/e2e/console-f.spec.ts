import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * The seats pane, the palette and the status bar, in a browser, against the
 * seeded store (CONSOLE.md section 9's package F acceptance).
 *
 * The vitest files beside this one check what the stores decide; this checks the
 * four things only a real page can: that the list costs one request and not one
 * per row or per screen, that an older server (404 on `/v1/seats`) still gets a
 * list and a sentence rather than an empty pane or a badge nobody computed, that
 * collapsing survives a reload, and that the palette is reachable and drivable
 * from the keyboard with a name and ids a screen reader can follow.
 *
 * Seats are read from the API rather than typed in: the fixtures decide which
 * seats exist, so a name written here would be the next thing to go stale.
 */

const SEATS = '**/v1/seats';

/** The seats pane, wherever it is drawn: the list screen or beside a seat. */
const pane = (page: Page) => page.getByRole('complementary', { name: 'Seats' });
const seats = (page: Page) => pane(page).locator('a.seat');

async function names(page: Page): Promise<string[]> {
  const answer = await page.request.get('/v1/seats');
  expect(answer.ok(), 'the fixtures have no seats').toBe(true);
  const rows = (await answer.json()) as { name: string }[];
  expect(rows.length, 'the fixtures have no seats').toBeGreaterThan(0);
  return rows.map((row) => row.name);
}

/** Open one seat's screen, which is where the pane is drawn on a desktop. */
async function openSeat(page: Page, name: string): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/seats/${encodeURIComponent(name)}`);
  await expect(page).toHaveURL(new RegExp(`/seats/${encodeURIComponent(name)}$`));
  await expect(seats(page).first()).toBeVisible();
}

test('one request paints the seats pane, and moving between seats adds none', async ({ page }) => {
  const rows = await names(page);
  const asked: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/v1/seats') asked.push(request.url());
  });

  // `/seats` on a desktop answers "which seat?" and opens it, and the pane
  // beside it is the list: the redirect must not buy a second list request.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/seats');
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);
  await expect(seats(page)).toHaveCount(rows.length);

  expect(asked, 'the seats list was fetched more than once').toHaveLength(1);

  if (rows.length > 1) {
    const second = rows.find((name) => !page.url().endsWith(`/${encodeURIComponent(name)}`));
    await seats(page).filter({ hasText: second as string }).first().click();
    await expect(page).toHaveURL(new RegExp(`/seats/${encodeURIComponent(second as string)}$`));
    expect(asked, 'opening another seat asked for the list again').toHaveLength(1);
  }
});

test('an older server that 404s the list still gets a list and says what it cannot say', async ({
  page
}) => {
  const rows = await names(page);

  await page.route(SEATS, (route) =>
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'not_found', message: 'no such route' } })
    })
  );

  await openSeat(page, rows[0]);

  await expect(pane(page)).toContainText('This server cannot say which seats are in step.');
  await expect(seats(page)).toHaveCount(rows.length);
  // the fallback path has no lineup, so there is no difference to claim
  await expect(pane(page).locator('.badge')).toHaveCount(0);
});

test('a badge is drawn for a count the server sent, and never for one it did not', async ({
  page
}) => {
  const rows = await names(page);
  const answer = await page.request.get('/v1/seats');
  const listed = (await answer.json()) as { name: string; changes: number | null; mode: string }[];

  await openSeat(page, rows[0]);

  for (const row of listed) {
    const link = seats(page).filter({ hasText: row.name }).first();
    await expect(link).toBeVisible();
    const badge = link.locator('.badge');
    if (typeof row.changes === 'number' && row.changes > 0) {
      await expect(badge, `${row.name} is out of step and carries no count`).toHaveText(
        String(row.changes)
      );
    } else if (row.mode === 'auto') {
      await expect(badge, `${row.name} carries a badge nobody computed`).toHaveCount(0);
    }
  }
});

test('which groups are collapsed is remembered across a reload', async ({ page }) => {
  const rows = await names(page);
  await openSeat(page, rows[0]);

  const heads = pane(page).locator('button.head');
  const first = heads.first();
  await expect(first).toHaveAttribute('aria-expanded', 'true');

  await first.click();
  await expect(first).toHaveAttribute('aria-expanded', 'false');

  await page.reload();

  await expect(pane(page).locator('button.head').first()).toHaveAttribute('aria-expanded', 'false');
});

test('the palette opens on Ctrl+K and on `/`, moves, runs, and hands focus back', async ({
  page
}) => {
  const rows = await names(page);
  await openSeat(page, rows[0]);

  const link = seats(page).first();
  await link.focus();
  await expect(link).toBeFocused();

  await page.keyboard.press('Control+k');
  const entry = page.getByRole('combobox', { name: 'Find a model, a seat or an action' });
  await expect(entry).toBeFocused();

  await entry.fill('Runs');
  await expect(page.locator('#palette-list [role="option"]').first()).toBeVisible();
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL(/\/runs$/);

  // and the shortcut nobody has to be told about. Named, because this screen
  // has selects of its own and a bare `combobox` is five of them.
  await page.keyboard.press('/');
  await expect(page.getByRole('combobox', { name: 'Find a model, a seat or an action' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('Esc gives focus back to whatever had it, and the palette is named for a screen reader', async ({
  page
}) => {
  const rows = await names(page);
  await openSeat(page, rows[0]);

  const link = seats(page).first();
  await link.focus();
  await page.keyboard.press('Control+k');

  // an accessible name, which is what a combobox has to have
  const entry = page.getByRole('combobox', { name: 'Find a model, a seat or an action' });
  await expect(entry).toBeVisible();
  await expect(entry).toHaveAttribute('aria-controls', 'palette-list');

  // and every option has an id, so aria-activedescendant can point at one
  const options = page.locator('#palette-list [role="option"]');
  const count = await options.count();
  expect(count, 'the palette offered nothing to run').toBeGreaterThan(0);
  for (let at = 0; at < count; at += 1) {
    expect(await options.nth(at).getAttribute('id')).toBe(`palette-${at}`);
  }

  const first = await entry.getAttribute('aria-activedescendant');
  await page.keyboard.press('ArrowDown');
  await expect(entry).not.toHaveAttribute('aria-activedescendant', first as string);

  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(link).toBeFocused();
});

test('the status bar says nothing about the fields the server did not send', async ({ page }) => {
  await page.route('**/v1/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      // every field the route always answers with, and nothing else
      body: JSON.stringify({
        pulled_at: '2026-09-19T04:00:00Z',
        ran_at: '2026-09-19T04:06:00Z',
        schedule: 'hourly',
        sources_enabled: 3,
        telemetry_calls: 4015,
        telemetry_at: '2026-09-19T04:06:00Z'
      })
    })
  );

  const rows = await names(page);
  await openSeat(page, rows[0]);

  const bar = page.getByRole('contentinfo', { name: 'Status' });
  await expect(bar).toContainText('telemetry 4,015 calls');
  await expect(bar).not.toContainText('reachable');
  await expect(bar).not.toContainText('next run');
  await expect(bar).not.toContainText('Cannot reach the API');
});

test('the status bar says so when the API cannot be reached at all', async ({ page }) => {
  await page.route('**/v1/status', (route) => route.abort('failed'));

  const rows = await names(page);
  await openSeat(page, rows[0]);

  await expect(page.getByRole('contentinfo', { name: 'Status' })).toContainText(
    'Cannot reach the API'
  );
});
