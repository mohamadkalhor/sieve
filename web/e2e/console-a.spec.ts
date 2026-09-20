import { expect, test } from '@playwright/test';

/**
 * The shell, in a browser, because that is the only place it exists.
 *
 * Four claims, all of them things a component test cannot see:
 *
 *   1. every screen still draws, inside the shell, and throws nothing on the
 *      way -- the rail is gone and the sections moved into the top bar, which
 *      is exactly the kind of change that leaves one route reaching for a
 *      component that no longer exists;
 *   2. the addresses that moved still land where the brief says they land;
 *   3. the palette opens from the keyboard and gives the window back on
 *      Escape, which is the one behaviour the whole console leans on;
 *   4. `/seats` answers "which seat?" on a desktop and is the list itself in a
 *      narrow window.
 *
 * `pageerror` is collected rather than asserted after the fact: a screen that
 * renders its heading and then dies in an effect looks fine to a visibility
 * check.
 */

const SCREENS = [
  '/',
  '/seats',
  '/field',
  '/sources',
  '/unscored',
  '/connectors',
  '/runs',
  '/axes',
  '/guide'
];

/** the seat the seed fixtures have, so a seat URL has something to open */
const A_SEAT = 'coder';

for (const path of SCREENS) {
  test(`${path} draws inside the shell and throws nothing`, async ({ page }) => {
    const thrown: string[] = [];
    page.on('pageerror', (error) => thrown.push(String(error)));

    await page.goto(path);

    // `.topbar`, not `.top`: the Field screen has a header of its own, and a
    // spec that says `.top` matches both of them.
    await expect(page.locator('.topbar')).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Sections' })).toBeVisible();
    await expect(page.locator('main.stage')).toBeVisible();

    // the palette is part of the shell, not of any screen
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');

    expect(thrown, `${path} threw`).toEqual([]);
  });
}

for (const path of ['/', '/profiles', '/chains', '/rankings']) {
  test(`${path} lands on the seats list`, async ({ page }) => {
    await page.goto(path);
    await expect(page).toHaveURL(/\/seats(\/[^/]+)?$/);
    await expect(page.getByRole('navigation', { name: 'Sections' })).toBeVisible();
  });
}

test('an old profile URL lands on the seat of that name', async ({ page }) => {
  await page.goto(`/profiles/${A_SEAT}`);
  await expect(page).toHaveURL(new RegExp(`/seats/${A_SEAT}$`));

  // and the form the old links used, with the panel named in the query
  await page.goto(`/profiles?open=${A_SEAT}`);
  await expect(page).toHaveURL(new RegExp(`/seats/${A_SEAT}$`));
});

test('/seats reopens the seat this browser last worked in', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/seats/${A_SEAT}`);
  await expect(page).toHaveURL(new RegExp(`/seats/${A_SEAT}$`));
  // The key is written by an effect when the seat page renders, which is a beat
  // after the address bar changes: wait for the seat to be on screen before
  // leaving it, or the navigation wins the race.
  await expect(page.getByRole('heading', { level: 1 })).toContainText(A_SEAT);

  // somewhere else first, so the assertion cannot pass by accident
  await page.goto('/field');
  await page.goto('/seats');
  await expect(page).toHaveURL(new RegExp(`/seats/${A_SEAT}$`));
});

test('the palette takes the keyboard and gives it back', async ({ page }) => {
  await page.goto('/field');
  await expect(page.getByRole('dialog')).toHaveCount(0);

  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog');
  await expect(palette).toBeVisible();
  await expect(palette.getByRole('combobox')).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('/seats opens a seat on a desktop and is the list in a narrow window', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/seats');
  await expect(page).toHaveURL(/\/seats\/[^/]+$/);

  await page.setViewportSize({ width: 820, height: 900 });
  await page.goto('/seats');
  await expect(page).toHaveURL(/\/seats$/);
  await expect(page.getByRole('heading', { name: 'Seats' })).toBeVisible();
});

test('the console asks no other host for anything', async ({ page, baseURL }) => {
  // The fonts are the reason this exists: `--display` was Fraunces, which came
  // from a CDN by link tag, and CONSOLE.md section 3.2 puts both faces in the
  // bundle instead. A test that only looks for `fonts.gstatic.com` would pass
  // while some other asset quietly left; this one says nothing may.
  const home = new URL(baseURL ?? page.url()).host;
  const foreign: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith('http') && url.host !== home) foreign.push(request.url());
  });

  await page.goto('/seats');
  await page.goto('/field');
  await expect(page.locator('.topbar')).toBeVisible();

  expect(foreign).toEqual([]);
});
