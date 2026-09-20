/**
 * How wide the window is, and which of section 6.8's four layouts that means.
 *
 * The three widths in the brief are not arbitrary: 1280 is where three panes
 * fit without the table losing its columns, 1024 is where the inspector has to
 * stop taking a column of its own, and 900 is where the seats pane stops being
 * a pane and becomes a switcher in the seat's header. The rule is one pure
 * function so the test can walk the boundaries rather than the browser having
 * to.
 *
 * Only the pieces a stylesheet cannot decide are asked of this: whether the
 * inspector is a pane of its own or an overlay, and whether the seats pane is
 * drawn. Everything else -- columns, paddings, which columns the table shows --
 * is a media query, because a stylesheet that disagrees with this file about
 * 1280 would draw two inspectors and nothing would say so.
 */
export type Shape = 'three' | 'drawer' | 'switcher' | 'single';

/** three panes: seats, seat, inspector */
export const THREE = 1280;
/** the inspector becomes a drawer over the seat pane's right edge */
export const DRAWER = 1024;
/** the seats pane collapses into the seat header's switcher */
export const SWITCHER = 900;

export function shapeFor(width: number): Shape {
  if (width >= THREE) return 'three';
  if (width >= DRAWER) return 'drawer';
  if (width >= SWITCHER) return 'switcher';
  return 'single';
}

/**
 * What the server draws, and what the browser draws before it has measured
 * itself. Three panes is the reading that is never wrong, only wide: every
 * route is reachable in it, and the overlay below 1280 is drawn from a person's
 * own selection, which no server render has.
 */
const DEFAULT_WIDTH = THREE;

/**
 * The one window measurement in the console. It is a singleton rather than a
 * context value because both readers (the seat route and the seat header) can
 * be on screen at once and must agree; it listens for as long as the tab lives,
 * which is what a resize listener on a fixed number is for.
 */
export class Viewport {
  width = $state(DEFAULT_WIDTH);
  readonly shape = $derived(shapeFor(this.width));

  constructor() {
    if (typeof window === 'undefined') return;
    this.width = window.innerWidth;
    window.addEventListener('resize', () => {
      this.width = window.innerWidth;
    });
  }
}

let only: Viewport | null = null;

/** The console's window, created on first use. */
export function viewport(): Viewport {
  if (!only) only = new Viewport();
  return only;
}
