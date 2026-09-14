import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Three widths, four screens, and one rule: the page never scrolls sideways.
 *
 * The screenshots are the point as much as the assertions -- overlap is a
 * thing you see -- so they are written somewhere a person can open them.
 */

const SHOTS = process.env.SIEVE_SHOTS ?? '/tmp/ams-31/shots';

const WIDTHS = [
  { name: '390', width: 390, height: 900 },
  { name: '820', width: 820, height: 1000 },
  { name: '1280', width: 1280, height: 1000 }
];

const SCREENS = [
  { name: 'profiles', path: '/profiles' },
  { name: 'profile-coder', path: '/profiles/coder' },
  { name: 'connectors', path: '/connectors' },
  { name: 'runs', path: '/runs' }
];

test.beforeAll(() => {
  mkdirSync(SHOTS, { recursive: true });
});

/**
 * Anything sticking out past the right edge, ignoring what is allowed to: a
 * `.scroll-x` box scrolls on purpose, and an off-screen label is how a
 * screen-reader-only string is hidden.
 */
async function spilling(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const edge = window.innerWidth + 2;
    const out: string[] = [];
    for (const node of Array.from(document.body.querySelectorAll<HTMLElement>('*'))) {
      if (node.closest('.scroll-x, [data-scrolls]')) continue;
      const style = getComputedStyle(node);
      if (style.position === 'absolute' || style.position === 'fixed') continue;
      if (style.overflowX === 'auto' || style.overflowX === 'scroll') continue;
      const rect = node.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      if (rect.right > edge) {
        out.push(`${node.tagName.toLowerCase()}.${node.className || '(no class)'} → ${Math.round(rect.right)}px`);
      }
    }
    return out.slice(0, 8);
  });
}

for (const size of WIDTHS) {
  for (const screen of SCREENS) {
    test(`${screen.name} at ${size.name}px does not scroll sideways`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.goto(screen.path);
      await expect(page.locator('section[aria-label="Runs"]')).toBeVisible();
      // let the lists arrive before measuring anything
      await page.waitForLoadState('networkidle');

      const path = join(SHOTS, `${screen.name}-${size.name}.png`);
      await page.screenshot({ path, fullPage: true });

      const wide = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 2
      );
      expect(wide, 'the document is wider than the window').toBe(false);
      expect(await spilling(page)).toEqual([]);
    });
  }
}

test('the rail runs the full height of a long page', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/profiles');
  await page.waitForLoadState('networkidle');

  const measured = await page.evaluate(() => {
    const rail = document.querySelector('nav[aria-label="Sections"]')?.parentElement;
    return {
      rail: rail ? rail.getBoundingClientRect().height : 0,
      page: document.body.getBoundingClientRect().height
    };
  });

  // the ribbon used to stop one screen down; it has to reach the bottom
  expect(measured.page).toBeGreaterThan(0);
  expect(measured.rail).toBeGreaterThanOrEqual(measured.page - 2);
});
