import { expect, test, type Page } from '@playwright/test';

/**
 * Part 8C: two searches over the Field, and one model's effort modes joined
 * into a line.
 *
 * The question these answer is "how does effort affect price", and the
 * uncomfortable half of the answer is that on **posted** price it mostly does
 * not: a family charges one rate for every mode, so that line is vertical. It
 * is the cost of a *task* that moves, because a harder mode returns more
 * tokens at the same rate — and only the gateway's own traffic knows how many.
 *
 * So these assertions are made against the pixels the component actually drew,
 * not against numbers recomputed beside it. A test that recomputes the chart's
 * own arithmetic and then agrees with itself proves nothing.
 */

/**
 * Where the lit (green) marks are, read off the canvas.
 *
 * `--good` is #5dbb8a. Nothing else on this canvas is green: the dimmed field
 * is grey, the seat ring is amber, and the mode labels are ink.
 */
async function litSpread(page: Page): Promise<{ span: number; columns: number; pixels: number }> {
  return page.evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement;
    const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
    const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const xs = new Set<number>();
    let pixels = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const i = (y * width + x) * 4;
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        if (data[i + 3] > 200 && g > 130 && g - r > 35 && g - b > 20) {
          xs.add(x);
          pixels += 1;
        }
      }
    }
    const sorted = [...xs].sort((a, b) => a - b);
    const dpr = canvas.width / canvas.getBoundingClientRect().width;
    return {
      span: sorted.length ? (sorted[sorted.length - 1] - sorted[0]) / dpr : 0,
      columns: sorted.length,
      pixels
    };
  });
}

test('the field starts unfiltered: no search, no clear button, nothing lit', async ({ page }) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  await expect(page.getByLabel('Provider', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Clear' })).toHaveCount(0);
  expect((await litSpread(page)).pixels, 'no search, no green').toBe(0);
});

test('both boxes offer their options, so nobody types an id from memory', async ({ page }) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();

  const providers = page.locator('#field-providers option');
  expect(await providers.count()).toBeGreaterThan(3);
  const names = await providers.evaluateAll((o) => o.map((x) => (x as HTMLOptionElement).value));
  expect(names).toContain('openai');
  expect([...names].sort()).toEqual(names); // sorted, so the list is scannable

  const before = await page.locator('#field-models option').count();
  await page.getByLabel('Provider', { exact: true }).fill('openai');
  await expect.poll(() => page.locator('#field-models option').count()).toBeLessThan(before);
  const scoped = await page
    .locator('#field-models option')
    .evaluateAll((o) => o.map((x) => (x as HTMLOptionElement).value));
  expect(scoped.length).toBeGreaterThan(0);
  expect(scoped.every((id) => id.startsWith('openai/'))).toBe(true);
});

test('a provider lights its models and greys the rest, without removing them', async ({
  page,
  request
}) => {
  const models = await (await request.get('/v1/models?modality=llm&limit=1000')).json();
  const openai = models.items.filter((m: { creator: string }) => m.creator === 'openai').length;
  expect(openai).toBeGreaterThan(5);

  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  const plotted = Number(
    (await page.locator('figure.field').getAttribute('aria-label'))?.match(/^(\d+)/)?.[1]
  );

  await page.getByLabel('Provider', { exact: true }).fill('openai');
  await expect(page.locator('.count[role="status"]')).toHaveText(`${openai} lit`);
  await expect.poll(async () => (await litSpread(page)).pixels).toBeGreaterThan(0);

  // grey means dimmed and still there: the rest of the field is what makes the
  // green mean anything, so the plotted population must not have shrunk
  await expect(page.locator('figure.field')).toHaveAttribute(
    'aria-label',
    new RegExp(`^${plotted} models plotted`)
  );
  expect(plotted).toBeGreaterThan(openai);
});

test('a model narrows to that family, and clearing gives the whole field back', async ({
  page,
  request
}) => {
  const models = await (await request.get('/v1/models?modality=llm&limit=1000')).json();
  const family = 'openai/gpt-6-astra';
  const modes = models.items.filter((m: { family: string }) => m.family === family);
  expect(modes.length, 'astra publishes several effort modes').toBeGreaterThan(3);

  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  const whole = await page.locator('figure.field').getAttribute('aria-label');

  await page.getByLabel('Model', { exact: true }).fill(family);
  await expect(page.locator('.count[role="status"]')).toHaveText(`${modes.length} lit`);

  // the axis does not rescale to the family: a point must not move on screen
  // because something unrelated left it
  await expect(page.locator('figure.field')).toHaveAttribute('aria-label', whole as string);

  await page.getByRole('button', { name: 'Clear' }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Clear' })).toHaveCount(0);
  await expect.poll(async () => (await litSpread(page)).pixels).toBe(0);
});

test('a model outside the chosen provider does not widen the search', async ({ page }) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();

  await page.getByLabel('Provider', { exact: true }).fill('anthropic');
  const anthropic = await page.locator('.count[role="status"]').innerText();

  await page.getByLabel('Model', { exact: true }).fill('openai/gpt-6-astra');
  // still anthropic: an openai family is not inside the anthropic scope
  await expect(page.locator('.count[role="status"]')).toHaveText(anthropic);
});

test('the Field opens on raw data, with no prose between the controls and the chart', async ({
  page
}) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();

  await expect(page.locator('#view'), 'raw data unless someone asks for a profile').toHaveValue(
    'raw'
  );
  // the posted price, named on the chart, and no profile anywhere in the title
  const label = (await page.locator('figure.field').getAttribute('aria-label')) ?? '';
  expect(label).toContain('posted price per 1M tokens');
  expect(label).not.toMatch(/cheap_bulk|task/);

  // the sentences that used to sit under the controls are gone
  await expect(page.locator('.describes')).toHaveCount(0);
  await expect(page.locator('.rates')).toHaveCount(0);
});

test('raw data and a profile view disagree, and the pixels show it', async ({ page }) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByLabel('Model', { exact: true }).fill('openai/gpt-6-astra');
  await expect(page.locator('.count[role="status"]')).toContainText('lit');

  // the price list: one rate for every mode, so the modes sit over one x
  const posted = await litSpread(page);
  expect(posted.pixels).toBeGreaterThan(0);

  // a profile's task: each mode burns a different number of tokens, so they spread
  await page.locator('#view').selectOption('reasoner');
  await expect(page.locator('#axis'), 'a profile view opens on its own score').toHaveValue(
    '__score__'
  );
  const perTask = await litSpread(page);
  expect(perTask.pixels).toBeGreaterThan(0);

  console.log(`astra span — posted ${posted.span}px, reasoner task ${perTask.span}px`);
  expect(posted.span, 'astra charges one rate for every mode').toBeLessThan(24);
  expect(perTask.span, 'telemetry separates the modes').toBeGreaterThan(posted.span * 2);
});

test('telemetry is what separates them, and the ranking says which points it has', async ({
  request
}) => {
  const ranking = await (await request.get('/v1/rankings/reasoner')).json();
  const astra = (ranking.ranks ?? []).filter((r: { model_id: string }) =>
    r.model_id.startsWith('openai/gpt-6-astra-')
  );
  expect(astra.length, 'the reasoner profile must rank astra').toBeGreaterThan(1);

  const measured = astra.filter((r: { cost_from: string }) => r.cost_from === 'telemetry');
  expect(measured.length, 'the smoke store seeds traffic for every astra mode').toBeGreaterThan(1);

  // those costs differ even though the posted rate is identical, which is the
  // whole claim a profile's cost-per-task axis makes
  const costs = new Set(measured.map((r: { cost_per_task: number }) => r.cost_per_task));
  expect(costs.size).toBe(measured.length);
});

test('no horizontal scroll at 390px with both searches on screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByLabel('Provider', { exact: true }).fill('openai');

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(overflow).toBe(false);
});

test('a profile view costs that profile task, at the numbers its ranking gives', async ({
  page,
  request
}) => {
  await page.goto('/field');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByLabel('Model', { exact: true }).fill('openai/gpt-6-astra');

  /** The costs that profile's ranking gives astra's effort modes. */
  const astra = async (profile: string): Promise<number[]> => {
    const ranking = await (await request.get(`/v1/rankings/${profile}`)).json();
    return ((ranking.ranks ?? []) as { model_id: string; cost_per_task?: number | null }[])
      .filter((rank) => rank.model_id.startsWith(`openai/gpt-6-astra-`) && rank.cost_per_task != null)
      .map((rank) => rank.cost_per_task as number);
  };

  const seen: { profile: string; low: number; high: number }[] = [];
  for (const profile of ['cheap_bulk', 'reader']) {
    await page.locator('#view').selectOption(profile);
    // a profile view opens on that profile's own score: the axis that says what
    // one of its tasks costs is the cost axis
    await page.locator('#axis').selectOption('cost');
    await expect(page.locator('figure.field')).toHaveAttribute(
      'aria-label',
      new RegExp(`one ${profile} task`)
    );

    const [low, high] = await domain(page);
    seen.push({ profile, low, high });

    // the axis is that profile's ranking, not the posted rate beside it: its
    // bounds have to cover astra's modes at cost-per-task money, not per-1M
    const costs = await astra(profile);
    expect(costs.length, 'astra publishes several effort modes').toBeGreaterThan(3);
    expect(low, `${profile}: the axis does not reach its cheapest mode`).toBeLessThanOrEqual(
      Math.min(...costs) + 1e-9
    );
    expect(high, `${profile}: the axis does not reach its dearest mode`).toBeGreaterThanOrEqual(
      Math.max(...costs) - 1e-9
    );
  }

  // The spread among the modes is the telemetry's, not the rate's: every mode
  // is served at one price per token, so only the tokens a mode actually burns
  // separate them. What no longer separates the two profiles is the task shape:
  // `SHAPES` (engine.py) is one shape per modality now -- 8k in, 2k out for llm
  // -- not the per-profile control this file was written against, so the same
  // model costs the same under both and the two spreads agree.
  const spread = (one: { low: number; high: number }) => one.high / one.low;
  console.log(
    `astra cost spread — cheap_bulk ${spread(seen[0]).toFixed(2)}x, reader ${spread(seen[1]).toFixed(2)}x`
  );
  expect(spread(seen[0]), 'at one task shape, effort is most of the bill').toBeGreaterThan(1.5);
  expect(spread(seen[1]), 'one shape costs every profile').toBeCloseTo(spread(seen[0]), 2);
});

test('the same screen, reloaded, draws the same axis', async ({ page }) => {
  // this used to merge nine profiles' rankings and keep whichever fetch
  // resolved first, so the cost axis changed between reloads of one page
  const seen = new Set<string>();
  for (let i = 0; i < 3; i++) {
    await page.goto('/field');
    await expect(page.locator('canvas')).toBeVisible();
    seen.add((await page.locator('figure.field').getAttribute('data-cost-domain')) ?? '');
  }
  expect([...seen], 'one domain across three loads').toHaveLength(1);
});

/** The cost axis's own bounds, in dollars per task, with the padding undone. */
async function domain(page: Page): Promise<[number, number]> {
  const raw = (await page.locator('figure.field').getAttribute('data-cost-domain')) ?? '0..0';
  const [low, high] = raw.split('..').map(Number);
  // the domain is padded 0.8 / 1.2 either side, so undo that to get the data
  return [low / 0.8, high / 1.2];
}
