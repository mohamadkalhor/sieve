/**
 * The four layouts of section 6.8, and the three widths that divide them.
 *
 * The boundaries are the whole point of this file: an off-by-one at 1024 puts
 * the seats pane and the switcher on screen together, and at 1280 it puts two
 * inspectors on screen. So each boundary is checked on both sides rather than
 * at a comfortable width in the middle of the range.
 */
import { describe, expect, it } from 'vitest';
import { DRAWER, SWITCHER, THREE, shapeFor } from '../../src/lib/console/layout/viewport.svelte';

describe('shapeFor', () => {
  it('keeps three panes from 1280 up', () => {
    expect(shapeFor(1280)).toBe('three');
    expect(shapeFor(1440)).toBe('three');
    expect(shapeFor(3840)).toBe('three');
  });

  it('draws the inspector over the seat pane from 1024 to 1279', () => {
    expect(shapeFor(1279)).toBe('drawer');
    expect(shapeFor(1024)).toBe('drawer');
    expect(shapeFor(1200)).toBe('drawer');
  });

  it('collapses the seats pane into the switcher from 900 to 1023', () => {
    expect(shapeFor(1023)).toBe('switcher');
    expect(shapeFor(900)).toBe('switcher');
    expect(shapeFor(960)).toBe('switcher');
  });

  it('is one column below 900', () => {
    expect(shapeFor(899)).toBe('single');
    expect(shapeFor(768)).toBe('single');
    expect(shapeFor(375)).toBe('single');
  });

  it('names the widths the brief names', () => {
    expect([SWITCHER, DRAWER, THREE]).toEqual([900, 1024, 1280]);
  });
});
