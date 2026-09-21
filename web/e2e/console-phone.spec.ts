import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';

/**
 * The seat on a phone (CONSOLE.md section 6.8), in a browser, against the
 * seeded store.
 *
 * The vitest beside it checks what the table decides to draw; this checks the
 * things only a real page at a real width can show: that the header stacks
 * instead of squeezing the title down to its first five characters and the
 * purpose down to one word per line, that the hint about dragging a divider is
 * not on a screen without a divider to drag, that the row's buttons are in the
 * sheet rather than on top of the score, that the Lineup switch starts where
 * the table's own cells start, and that the status bar cuts nothing off at the
 * edge.
 *
 * Every reading is a box measured from the page; no number here is written
 * down twice. The desk is measured as well, because the three changes that are
 * only about the phone must not have moved it.
 */

const SHIP_TABLE = { name: 'What these settings would ship' };
const PHONE = { width: 375, height: 812 };
const DESK = { width: 1440, height: 900 };

type Box = { x: number; y: number; width: number; height: number };

/** The seat the fixtures seed for a modality, by modality -- as console-e does. */
async function seatFor(page: Page, modality: string): Promise<string> {
  const answer = await page.request.get('/v1/seats');
  expect(answer.ok(), 'the fixtures have no seats').toBe(true);
  const seats = (await answer.json()) as { name: string; modality: string }[];
  const seat = seats.find((entry) => entry.modality === modality);
  if (!seat) throw new Error(`no ${modality} seat in the fixtures`);
  return seat.name;
}

/** Open the llm seat at this width, and wait until its table is drawn. */
async function openSeat(page: Page, viewport: { width: number; height: number }): Promise<string> {
  const seat = await seatFor(page, 'llm');
  await page.setViewportSize(viewport);
  await page.goto(`/seats/${seat}`);
  await expect(page.getByRole('table', SHIP_TABLE)).toBeVisible();
  return seat;
}

async function box(locator: Locator, what: string): Promise<Box> {
  const measured = await locator.boundingBox();
  if (!measured) throw new Error(`${what} is not drawn`);
  return measured;
}

/** How much of its own content an element cannot show. */
function hidden(locator: Locator): Promise<number> {
  return locator.evaluate((el) => el.scrollWidth - el.clientWidth);
}

/** The area two boxes share, 0 when they only touch. */
function shared(a: Box, b: Box): number {
  const across = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const down = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return across > 1 && down > 1 ? across * down : 0;
}

/** Whether two boxes are on one line. */
function sameRow(a: Box, b: Box): boolean {
  return Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0;
}

const rows = (page: Page) => page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
const head = (page: Page) => page.getByRole('table', SHIP_TABLE).locator('.head');

test('the header stacks on a phone and is still one row on a desk', async ({ page }) => {
  await openSeat(page, PHONE);

  // `header.head` and not `header`: the top bar is a header too, and it has a
  // `.name` of its own (who is signed in).
  const title = page.locator('header.head .name');
  const purpose = page.locator('header.head .purpose');
  const modes = page.getByRole('radiogroup', { name: 'List mode' });
  const ship = page.getByRole('button', { name: /ship/i }).first();
  const menu = page.locator('header.head .menu button').first();

  await expect(title).toBeVisible();
  await expect(purpose).toBeVisible();
  expect(await hidden(title), 'the title is cut off').toBeLessThanOrEqual(1);

  const [t, p, m, s, k] = await Promise.all([
    box(title, 'the title'),
    box(purpose, 'the purpose'),
    box(modes, 'the Auto/Manual toggle'),
    box(ship, 'the ship button'),
    box(menu, 'the seat menu')
  ]);

  // the purpose has a line of its own, under the title, at the width of the seat
  expect(p.y, 'the purpose still shares the title line').toBeGreaterThanOrEqual(t.y + t.height - 1);
  expect(p.width, 'the purpose is not full width').toBeGreaterThan((PHONE.width - 32) * 0.9);

  // the toggle and the button have the line under it, and the button takes the
  // rest of it -- measured against the line's own right edge, which is the
  // purpose's, rather than against the window
  expect(m.y, 'the toggle is not under the purpose').toBeGreaterThanOrEqual(p.y + p.height - 1);
  expect(s.x, 'the ship button is not beside the toggle').toBeGreaterThanOrEqual(m.x + m.width - 1);
  expect(s.x + s.width, 'the ship button does not reach the right edge').toBeGreaterThanOrEqual(
    p.x + p.width - 1
  );
  expect(s.width, 'the ship button is not the wider of the two').toBeGreaterThan(m.width);

  // and the menu is up on the title's line, at the right edge
  expect(k.y, 'the menu is not on the title line').toBeLessThan(p.y);
  await expect(menu).toBeInViewport();

  // the desk: the toggle is beside the title again, and nothing is clipped
  await openSeat(page, DESK);
  const [dt, dp, dm] = await Promise.all([
    box(title, 'the title'),
    box(purpose, 'the purpose'),
    box(modes, 'the Auto/Manual toggle')
  ]);
  expect(await hidden(title), 'the title is cut off on the desk').toBeLessThanOrEqual(1);
  expect(sameRow(dt, dm), 'the desk header lost its single row').toBe(true);
  expect(dp.y, 'the desk purpose moved above the title').toBeGreaterThanOrEqual(
    dt.y + dt.height - 1
  );
});

test('the weights hint is not on a phone and the presets take their own line', async ({ page }) => {
  await openSeat(page, PHONE);

  const hint = page.getByText(/drag a divider/i);
  // The Must block's label is a `.cap` under a `.block` as well, so this one is
  // found by the row it is in: the head that holds the presets.
  const cap = page.locator('.presets').locator('..').locator('.cap');
  const even = page.getByRole('button', { name: 'Even' });

  await expect(cap).toBeVisible();
  await expect(hint, 'the drag hint is drawn on the phone').toBeHidden();

  const [c, e] = await Promise.all([box(cap, 'the WEIGHTS label'), box(even, 'the Even preset')]);
  expect(e.y, 'the presets still share the label line').toBeGreaterThanOrEqual(c.y + c.height - 1);
  expect(e.x, 'the presets are not at the left edge').toBeLessThan(c.x + c.width + 40);

  // the desk keeps the sentence
  await openSeat(page, DESK);
  await expect(hint, 'the drag hint is gone from the desk').toBeVisible();
});

test('the row on a phone is the rank, the model and the score', async ({ page }) => {
  await openSeat(page, PHONE);

  const first = rows(page).first();
  await expect(first).toBeVisible();
  await expect(first.locator('.cell.rank')).toBeVisible();
  await expect(first.locator('.cell.model')).toBeVisible();
  await expect(first.locator('.cell.score')).toBeVisible();

  for (const gone of ['.cell.task', '.cell.can', '.cell.via', '.cell.acts']) {
    await expect(first.locator(gone), `${gone} is still drawn in the phone row`).toBeHidden();
  }

  // nothing in the row sits on the score
  const score = await box(first.locator('.cell.score .num.figure'), 'the score');
  for (const button of await first.locator('button').all()) {
    if (!(await button.isVisible())) continue;
    const where = await box(button, 'a row button');
    expect(shared(where, score), 'a row button sits on the score').toBe(0);
  }

  // the tags are under the name, not beside it
  const name = first.locator('.cell.model .name');
  const tags = first.locator('.cell.model .tag');
  if ((await tags.count()) > 0) {
    const [n, g] = await Promise.all([box(name, 'the name'), box(tags.first(), 'a tag')]);
    expect(g.y, 'a tag is still beside the name').toBeGreaterThanOrEqual(n.y + n.height - 1);
  }

  // the row still draws its buttons just above the phone width, and they do not
  // sit on the score there either -- which is what the box comparison is for
  await openSeat(page, { width: 900, height: 900 });
  const wide = rows(page).first();
  await expect(wide.locator('.cell.acts')).toBeVisible();
  const wideScore = await box(wide.locator('.cell.score .num.figure'), 'the score');
  for (const button of await wide.locator('.cell.acts button').all()) {
    const where = await box(button, 'a row button');
    expect(shared(where, wideScore), 'a row button sits on the score at 900px').toBe(0);
  }

  // the desk keeps the row's buttons
  await openSeat(page, DESK);
  await expect(rows(page).first().locator('.cell.acts')).toBeVisible();
});

test('the Lineup switch starts where the table does', async ({ page }) => {
  for (const view of [PHONE, DESK]) {
    await openSeat(page, view);
    const control = page.getByRole('radiogroup', { name: 'What the table shows' });
    await expect(control).toBeVisible();

    const [c, rule, at] = await Promise.all([
      box(control, 'the Lineup switch'),
      box(head(page), "the table's header row"),
      box(head(page).locator('.cell.at'), "the table's first column")
    ]);

    expect(Math.abs(c.x - at.x), 'the switch is out of line with the table').toBeLessThanOrEqual(2);
    expect(shared(c, rule), 'the switch overlaps the table').toBe(0);
  }
});

test('the status bar fits the phone and cuts nothing off', async ({ page }) => {
  await openSeat(page, PHONE);

  const bar = page.locator('footer.status');
  const shell = await box(bar, 'the status bar');
  expect(await hidden(bar), 'the bar itself scrolls sideways').toBeLessThanOrEqual(1);

  for (const child of await bar.locator('> *').all()) {
    if (!(await child.isVisible())) continue;
    const key = await child.getAttribute('data-key');
    expect(['run', 'unscored'], `the phone bar is showing "${key}"`).toContain(key);
    expect(await hidden(child), 'a status item is cut off').toBeLessThanOrEqual(1);

    const where = await box(child, 'a status item');
    expect(where.y + where.height, 'a status item is taller than the bar').toBeLessThanOrEqual(
      shell.y + shell.height + 1
    );
    expect(where.x + where.width, 'a status item runs past the edge').toBeLessThanOrEqual(
      shell.x + shell.width + 1
    );
  }

  // the desk keeps the whole bar
  await openSeat(page, DESK);
  await expect(bar.locator('[data-key="reachable"]')).toBeVisible();
  await expect(bar.locator('[data-key="telemetry"]')).toBeVisible();
});
