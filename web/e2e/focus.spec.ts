import { expect, test, type Page } from '@playwright/test';

/**
 * Every screen, walked with the Tab key alone, checking that you can always
 * see where you are.
 *
 * Brief C says "keyboard focus visible everywhere". That is not a thing a
 * static audit can answer: `:focus-visible` deliberately does *not* match
 * focus set by script, so `element.focus()` from a test reports no ring and
 * looks like a bug that is not there. The only honest way to check it is to
 * press Tab, which is what this file does.
 *
 * For each stop the walk asserts three things:
 *
 *   1. the browser considers it focus-visible, so the ring styles apply at all;
 *   2. something actually paints -- a non-`none` outline, or a box-shadow, or
 *      a ring drawn on a `::before`/`::after`. A width of 0 does not count;
 *   3. the ring is not clipped away. An `outline` drawn just outside an
 *      element sitting inside an `overflow: hidden` or `overflow: auto` box is
 *      invisible even though every computed style says it is there, so the
 *      walk compares the focused rect against its scroll-clipping ancestors.
 *
 * It also asserts the walk reaches a real number of stops on each screen, so
 * that a page which traps focus, or skips its controls entirely, fails rather
 * than passing on an empty list.
 */

const SCREENS = [
  { path: '/field', name: 'Field' },
  { path: '/profiles', name: 'Profiles' },
  { path: '/profiles/coder', name: 'Profile editor' },
  { path: '/rankings/coder', name: 'Rankings' },
  { path: '/chains', name: 'Chains' },
  { path: '/sources', name: 'Sources' }
];

/** the widest a ring can be drawn outside the element and still be a ring */
const RING_SLACK = 6;

type Stop = {
  tag: string;
  label: string;
  focusVisible: boolean;
  ring: string | null;
  clippedBy: string | null;
};

/**
 * Describe whatever currently has focus: what it is, whether the browser calls
 * it focus-visible, what it paints, and whether an ancestor cuts that off.
 */
async function describeFocused(page: Page): Promise<Stop | null> {
  return page.evaluate((slack) => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body || el === document.documentElement) return null;

    const label =
      el.getAttribute('aria-label') ??
      el.getAttribute('id') ??
      (el.textContent ?? '').trim().slice(0, 40) ??
      '';

    /** a ring is an outline with width, a box-shadow, or one drawn on a pseudo */
    function ringOf(node: Element): string | null {
      for (const pseudo of [null, '::before', '::after']) {
        const s = getComputedStyle(node, pseudo ?? undefined);
        const width = parseFloat(s.outlineWidth || '0');
        if (s.outlineStyle !== 'none' && width > 0) {
          return `outline ${s.outlineWidth} ${s.outlineStyle}${pseudo ?? ''}`;
        }
        if (s.boxShadow && s.boxShadow !== 'none') return `box-shadow${pseudo ?? ''}`;
      }
      return null;
    }

    /**
     * The nearest ancestors that scroll or hide overflow. If the focused box
     * plus its ring does not fit inside one of them, the ring is not on screen
     * even though the computed style says it exists.
     */
    function clippedBy(node: HTMLElement): string | null {
      const rect = node.getBoundingClientRect();
      let parent = node.parentElement;
      while (parent && parent !== document.body) {
        const s = getComputedStyle(parent);
        const clips = [s.overflowX, s.overflowY].some((o) => o === 'hidden' || o === 'auto' || o === 'scroll');
        if (clips) {
          const box = parent.getBoundingClientRect();
          const outside =
            rect.left < box.left - slack ||
            rect.top < box.top - slack ||
            rect.right > box.right + slack ||
            rect.bottom > box.bottom + slack;
          if (outside) return parent.tagName.toLowerCase() + (parent.className ? '.' + String(parent.className).split(' ')[0] : '');
        }
        parent = parent.parentElement;
      }
      return null;
    }

    return {
      tag: el.tagName.toLowerCase(),
      label,
      focusVisible: el.matches(':focus-visible'),
      ring: ringOf(el),
      clippedBy: clippedBy(el)
    };
  }, RING_SLACK);
}

/** Tab through a screen, collecting every stop until focus wraps or runs out. */
async function walk(page: Page, limit = 60): Promise<Stop[]> {
  const stops: Stop[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press('Tab');
    const stop = await describeFocused(page);
    if (!stop) break;
    const key = `${stop.tag}:${stop.label}`;
    if (seen.has(key) && stops.length > 3) break; // wrapped
    seen.add(key);
    stops.push(stop);
  }
  return stops;
}

for (const screen of SCREENS) {
  test(`${screen.name}: every Tab stop shows a focus ring that is not clipped`, async ({ page }) => {
    await page.goto(screen.path);
    await page.locator('body').click({ position: { x: 2, y: 2 } });
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

    const stops = await walk(page);

    // a screen with no reachable control is a failure, not a pass
    expect(stops.length, `${screen.name} had no Tab stops`).toBeGreaterThan(2);

    const noRing = stops.filter((s) => s.focusVisible && !s.ring);
    const clipped = stops.filter((s) => s.ring && s.clippedBy);
    const notVisible = stops.filter((s) => !s.focusVisible);

    console.log(
      `${screen.name}: ${stops.length} tab stops, ${noRing.length} without a ring, ` +
        `${clipped.length} with a clipped ring, ${notVisible.length} not focus-visible`
    );

    expect(noRing.map((s) => `${s.tag} "${s.label}"`), `${screen.name}: focused with no ring`).toEqual([]);
    expect(
      clipped.map((s) => `${s.tag} "${s.label}" clipped by ${s.clippedBy}`),
      `${screen.name}: ring drawn but clipped away`
    ).toEqual([]);
    expect(
      notVisible.map((s) => `${s.tag} "${s.label}"`),
      `${screen.name}: Tab reached it but :focus-visible did not match`
    ).toEqual([]);
  });
}

/**
 * The walk above only means something if it can fail. Strip every ring on the
 * busiest screen and the same detector must report each stop as ringless --
 * otherwise a stylesheet regression would sail through as six green ticks.
 */
test('the detector fails when the rings are taken away', async ({ page }) => {
  await page.goto('/profiles');
  await page.addStyleTag({
    content: `*, *::before, *::after { outline: none !important; box-shadow: none !important; }`
  });
  await page.locator('body').click({ position: { x: 2, y: 2 } });
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  const stops = await walk(page);
  expect(stops.length).toBeGreaterThan(2);

  const noRing = stops.filter((s) => s.focusVisible && !s.ring);
  console.log(`rings stripped: ${noRing.length} of ${stops.length} stops now report no ring`);
  expect(noRing.length).toBe(stops.length);
});
