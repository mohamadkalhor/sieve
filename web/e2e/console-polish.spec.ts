import { expect, test, type Locator, type Page } from '@playwright/test';

const TOKEN = 'ci-secret';
const SHIP_TABLE = { name: 'What these settings would ship' };

async function openCoder(page: Page, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto('/seats/coder');
  await expect(page.getByRole('table', SHIP_TABLE)).toBeVisible();
}

async function signIn(page: Page): Promise<void> {
  await page.locator('button.who').click();
  const field = page.getByPlaceholder('paste a bearer token');
  await field.fill(TOKEN);
  await page.keyboard.press('Escape');
  await expect(field).toHaveCount(0);
}

async function height(control: Locator): Promise<number> {
  const box = await control.boundingBox();
  if (!box) throw new Error('control is not drawn');
  return box.height;
}

test('the switcher layout and its table fit at every 900–1023px width', async ({ page }) => {
  for (const width of [900, 960, 1023]) {
    await openCoder(page, width);

    const overflow = await page.locator('body').evaluate(() =>
      Math.max(
        document.documentElement.scrollWidth,
        ...Array.from(document.body.querySelectorAll<HTMLElement>('*'), (node) =>
          Math.ceil(node.getBoundingClientRect().right)
        )
      ) - window.innerWidth
    );
    expect(overflow, `${width}px has content wider than the viewport`).toBeLessThanOrEqual(1);

    const pane = page.locator('section.seat');
    const table = page.getByRole('table', SHIP_TABLE);
    const [paneBox, tableBox] = await Promise.all([pane.boundingBox(), table.boundingBox()]);
    if (!paneBox || !tableBox) throw new Error(`the ${width}px seat or table is not drawn`);
    expect(tableBox.width, `the table does not fill the ${width}px seat pane`).toBeGreaterThanOrEqual(
      paneBox.width - 1
    );
  }
});

test('pin, remove and restore keep keyboard focus on the same model row', async ({ page }) => {
  await openCoder(page, 1280);
  await signIn(page);

  const table = page.getByRole('table', SHIP_TABLE);
  const rows = table.locator('[role="row"][data-row]');
  await rows.first().focus();
  await page.keyboard.press('ArrowDown');
  const id = await page.locator('[role="row"][data-row]:focus').getAttribute('data-row');
  if (!id) throw new Error('ArrowDown did not focus a model row');
  const same = table.locator(`[data-row="${id}"]`);

  await page.keyboard.press('p');
  await expect(same).toBeFocused();
  await expect(same).toHaveAttribute('data-tone', 'ship');

  await page.keyboard.press('Delete');
  await expect(same).toHaveAttribute('data-tone', 'removed');
  await expect(same).toBeFocused();

  await same.getByRole('button', { name: /^Restore / }).click();
  await expect(same).not.toHaveAttribute('data-tone', 'removed');
  await expect(same).toBeFocused();
});

test('phone purpose, presets and weight segments have phone-sized hit areas', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/seats/coder');
  await expect(page.getByRole('table', SHIP_TABLE)).toBeVisible();

  const controls = [
    page.locator('header.head .purpose'),
    page.getByRole('button', { name: 'Even' }),
    page.locator('.track .seg').first()
  ];
  for (const control of controls) {
    await expect(control).toBeVisible();
    expect(await height(control), await control.getAttribute('class')).toBeGreaterThanOrEqual(44);
  }
  expect(await height(page.locator('.track .seg').first()), 'weight bar height').toBeGreaterThanOrEqual(48);
});
