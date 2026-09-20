import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';

/**
 * The inspector, in a browser, against the seeded store (CONSOLE.md section
 * 6.7, REVIEW.md findings 9 and 11).
 *
 * The vitest in `tests/console/e-inspector.test.ts` checks what the panel
 * decides; this checks the things only a real page can show: that a selected
 * row fills the column beside it, that the two fallbacks (a server that does
 * not say how a score is made up, a card the route does not have) arrive as
 * sentences rather than as empty drawings, and that nothing on the panel is a
 * number the panel made up.
 *
 * Seats are found by modality rather than typed in, for the same reason
 * `console-d.spec.ts` does not hard-code one: the fixtures decide which seats
 * exist, so a name written here would be the next thing to go stale.
 */

const SHIP_TABLE = { name: 'What these settings would ship' };

/** The inspector column. */
const inspector = (page: Page) => page.getByRole('complementary', { name: 'Inspector' });

async function seatFor(page: Page, modality: string): Promise<string> {
  const answer = await page.request.get('/v1/seats');
  expect(answer.ok(), 'the fixtures have no seats').toBe(true);
  const seats = (await answer.json()) as { name: string; modality: string }[];
  const seat = seats.find((entry) => entry.modality === modality);
  if (!seat) throw new Error(`no ${modality} seat in the fixtures`);
  return seat.name;
}

/** Open a seat and click its first shipped row; answer with that row's model. */
async function openModel(page: Page, seat: string): Promise<{ id: string; name: string }> {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/seats/${seat}`);
  await expect(page).toHaveURL(new RegExp(`/seats/${seat}$`));

  const rows = page.getByRole('table', SHIP_TABLE).locator('[role="row"][data-row]');
  // The second shipped row, not the first: the seat selects the first one
  // itself when `?model=` is empty, so clicking that row would prove nothing.
  const row = rows.nth(1);
  await expect(row).toBeVisible();
  const id = (await row.getAttribute('data-row')) ?? '';
  const name = (await row.locator('.cell.model .name').innerText()).trim();
  expect(id, 'a shipped row with no id').not.toBe('');
  expect(name, 'a shipped row with no name').not.toBe('');

  await row.click();
  await expect(row, 'clicking a row did not select it').toHaveAttribute('data-selected', 'true');
  await expect(page).toHaveURL(/[?&]model=/);
  return { id, name };
}

/** Fail the test if the page threw while it was being driven. */
function watchForThrows(page: Page): string[] {
  const thrown: string[] = [];
  page.on('pageerror', (error) => thrown.push(error.stack ?? String(error)));
  return thrown;
}

const NOT_FOUND = JSON.stringify({ error: { code: 'not_found', message: 'no model' } });

/** A card whose four answers nobody reported, for the unknown case. */
function cardWithNoAnswers(id: string): string {
  return JSON.stringify({
    id,
    name: id,
    creator: 'fixtures',
    modality: 'llm',
    effort: null,
    family: null,
    price: null,
    abilities: {
      vision: { answer: null, yes: [], no: [] },
      reasoning: { answer: null, yes: [], no: [] },
      tools: { answer: null, yes: [], no: [] },
      structured_output: { answer: null, yes: [], no: [] }
    },
    context_window: null,
    served_by: [],
    scored: true
  });
}

/** The row the seat is showing for one id, straight from the server. */
async function listedRow(page: Page, seat: string, id: string) {
  const answer = await page.request.post(`/v1/profiles/${seat}/preview`, { data: {} });
  expect(answer.ok(), `the preview for ${seat} did not answer`).toBe(true);
  const body = (await answer.json()) as {
    models: Record<string, unknown>[];
    next: Record<string, unknown>[];
    pool?: Record<string, unknown>[];
  };
  const found = [...body.models, ...body.next, ...(body.pool ?? [])].find((row) => row.id === id);
  if (!found) throw new Error(`the preview for ${seat} has no row for ${id}`);
  return found;
}

test('a selected row fills the inspector, and the URL says which model', async ({ page }) => {
  const thrown = watchForThrows(page);
  const seat = await seatFor(page, 'llm');
  const model = await openModel(page, seat);

  const pane = inspector(page);
  await expect(pane).toBeVisible();
  await expect(pane.getByRole('heading', { level: 2 })).toHaveText(model.name);
  await expect(pane.getByText(/^Selected · /)).toBeVisible();

  for (const block of ['why', 'abilities', 'price', 'served-by', 'actions']) {
    await expect(pane.locator(`[data-block="${block}"]`)).toBeVisible();
  }
  await expect(pane.getByRole('heading', { name: 'What it can do, and who says so' })).toBeVisible();

  // the block that explains the score is headed with the *raw* score, because
  // its bars are the raw axes (finding 11)
  const row = await listedRow(page, seat, model.id);
  expect(row.raw, 'the store no longer reports a raw score').toBeDefined();
  await expect(pane.locator('[data-block="why"] h3')).toHaveText(
    `Where ${(row.raw as number).toFixed(2)} comes from`
  );

  // the row knows it is the selected one
  await expect(
    page.getByRole('table', SHIP_TABLE).locator(`[role="row"][data-row="${model.id}"]`)
  ).toHaveAttribute('data-selected', 'true');

  // the selection is URL state: it survives a reload
  await page.reload();
  await expect(inspector(page).getByRole('heading', { level: 2 })).toHaveText(model.name);

  expect(thrown, 'the seat page threw').toEqual([]);
});

test('nothing on the panel is a number the panel made up', async ({ page }) => {
  for (const modality of ['llm', 'image-to-video']) {
    const thrown = watchForThrows(page);
    const seat = await seatFor(page, modality);
    await openModel(page, seat);

    const pane = inspector(page);
    const text = await pane.innerText();
    expect(text, `${seat} drew a made-up number`).not.toMatch(/NaN/);
    expect(text, `${seat} drew an undefined`).not.toMatch(/undefined/);

    // every axis is either a real pair of numbers or a track that says it was
    // not measured: an axis nobody measured is never drawn as a zero
    const axes = pane.locator('[data-block="why"] [data-axis]');
    const count = await axes.count();
    for (let i = 0; i < count; i += 1) {
      const axis = axes.nth(i);
      const measured = await axis.getAttribute('data-measured');
      const row = await axis.innerText();
      if (measured === 'true') {
        expect(row, `${seat} axis ${i} drew a value that is not a number`).toMatch(
          /\d+(\.\d+)? \/ \d+(\.\d+)?$/
        );
      } else {
        expect(row, `${seat} axis ${i}`).toContain('not measured');
        expect(row, `${seat} axis ${i} drew an unmeasured axis as zero`).not.toMatch(/\b0\.0\b/);
      }
    }

    // the price block is figures or one sentence, never a row of dashes
    const priceText = (await pane.locator('[data-block="price"]').innerText()).trim();
    expect(priceText, `${seat} drew an empty price block`).not.toBe('');
    expect(priceText, `${seat} drew a price block of dashes`).not.toMatch(/^—/);

    expect(thrown, `${seat} threw while the inspector was open`).toEqual([]);
  }
});

test('a server that does not say how a score is made up gets the sentence, not empty bars', async ({
  page
}) => {
  // strip the axes out of the preview: the field is optional (section 4.2)
  await page.route(/\/v1\/profiles\/[^/]+\/preview$/, async (route: Route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    for (const key of ['models', 'next', 'blocked', 'removed', 'pool']) {
      const rows = body[key];
      if (Array.isArray(rows)) {
        for (const row of rows) delete (row as Record<string, unknown>).axes;
      }
    }
    await route.fulfill({ response, body: JSON.stringify(body) });
  });

  const seat = await seatFor(page, 'llm');
  await openModel(page, seat);

  const why = inspector(page).locator('[data-block="why"]');
  await expect(why).toContainText('This server does not say how a score is made up.');
  await expect(why.locator('[data-axis]')).toHaveCount(0);
});

test('a card the route does not have is built from the row, and says who did not report', async ({
  page
}) => {
  await page.route(/\/v1\/model-card(\?|$)/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: NOT_FOUND })
  );

  const seat = await seatFor(page, 'llm');
  await openModel(page, seat);

  const pane = inspector(page);
  // the abilities still draw, from the preview row (section 4.3's fallback)
  const abilities = pane.locator('[data-block="abilities"]');
  await expect(abilities).toBeVisible();
  await expect(abilities.getByText('source not reported').first()).toBeVisible();

  // and the panel does not dress the missing card up as a failed read
  await expect(pane.getByText('Try again')).toHaveCount(0);
});

test('a required need nobody answers draws unknown, and says what unknown costs', async ({
  page
}) => {
  // the seeded seats require no needs, so this one is asked for
  await page.route(/\/v1\/profiles\/[^/]+\/settings$/, async (route: Route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, body: JSON.stringify({ ...body, needs: ['tools'] }) });
  });
  await page.route(/\/v1\/model-card(\?|$)/, async (route) => {
    const id = new URL(route.request().url()).searchParams.get('id') ?? 'model';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: cardWithNoAnswers(id)
    });
  });

  const seat = await seatFor(page, 'llm');
  await openModel(page, seat);

  const abilities = inspector(page).locator('[data-block="abilities"]');
  await expect(abilities.getByText('unknown').first()).toBeVisible();
  await expect(abilities).toContainText('Unknown counts as no for this seat.');
});

test('a read that fails says so and can be asked again', async ({ page }) => {
  let attempts = 0;
  let failing = true;
  await page.route(/\/v1\/model-card(\?|$)/, async (route) => {
    if (failing) {
      attempts += 1;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'http_500', message: 'the store fell over' } })
      });
      return;
    }
    await route.continue();
  });

  const seat = await seatFor(page, 'llm');
  await openModel(page, seat);

  const pane = inspector(page);
  await expect(pane.getByText('the store fell over')).toBeVisible();
  // a failed read is not a model without a price
  await expect(pane.getByText('No posted price')).toHaveCount(0);

  failing = false;
  await pane.getByRole('button', { name: 'Try again' }).first().click();
  await expect(pane.getByText('the store fell over')).toHaveCount(0);
  await expect(pane.getByRole('heading', { name: 'What it can do, and who says so' })).toBeVisible();
  expect(attempts, 'the failing read was never made').toBeGreaterThan(0);
});
