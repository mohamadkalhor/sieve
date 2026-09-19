import { describe, expect, it } from 'vitest';
import {
  GAP_PX,
  STUB_PX,
  STUB_SHARE,
  fits,
  label,
  layout,
  parties,
  pxToShare,
  transfer,
  type Segment
} from '../../src/lib/console/logic/split';

/**
 * The weight bar's geometry (REVIEW.md finding 7).
 *
 * The property that matters is the one the page's layout depends on: the
 * segments plus the gaps are exactly as wide as the track, never wider, and
 * never negative -- a negative width is a segment drawn inside out. A drag has
 * to be reversible: the pixels a transfer is worth must be the pixels the
 * layout gave those shares, or the bar would drift under the pointer.
 *
 * The regime boundary is part of the contract, not an accident: a share below
 * `STUB_SHARE` is drawn at a fixed width with the room left over shared between
 * the proportional axes, so the same weights laid out at two widths that
 * disagree about who is a stub give two different mappings. Round trips are
 * therefore asserted inside one regime, as the acceptance requires.
 */

const AXES = ['quality', 'cost', 'agentic', 'latency'];

function weightsOf(...values: number[]): Record<string, number> {
  return Object.fromEntries(AXES.map((axis, index) => [axis, values[index]]));
}

function sumOf(segments: readonly Segment[]): number {
  return segments.reduce((sum, segment) => sum + segment.px, 0);
}

describe('layout', () => {
  it('fills the track exactly: segments plus gaps are barPx', () => {
    for (const barPx of [120, 240, 480, 961.5, 1200]) {
      for (const values of [
        [0.25, 0.25, 0.25, 0.25],
        [0.7, 0.2, 0.07, 0.03],
        [0.97, 0.01, 0.01, 0.01],
        [1, 0, 0, 0]
      ]) {
        const segments = layout(weightsOf(...values), AXES, barPx);
        expect(segments).toHaveLength(4);
        expect(sumOf(segments) + GAP_PX * 3).toBeCloseTo(barPx, 9);
      }
    }
  });

  it('never gives a segment a negative width, even when nothing fits', () => {
    const weights = weightsOf(0.4, 0.3, 0.2, 0.1);
    for (const barPx of [0, 1, 20, 41.5, 56, 60]) {
      for (const segment of layout(weights, AXES, barPx)) {
        expect(segment.px).toBeGreaterThanOrEqual(0);
        expect(Number.isFinite(segment.px)).toBe(true);
      }
    }
  });

  it('draws a stub at its minimum and keeps the proportions of the rest', () => {
    const weights = weightsOf(0.6, 0.3, 0.1, 0.01);
    const segments = layout(weights, AXES, 400);
    const stub = segments.find((segment) => segment.axis === 'latency');
    expect(stub?.stub).toBe(true);
    expect(stub?.px).toBe(STUB_PX);
    // the proportional axes share the room the stub did not take
    const room = 400 - GAP_PX * 3 - STUB_PX;
    expect(segments.find((s) => s.axis === 'quality')?.px).toBeCloseTo((0.6 / 1) * room, 9);
    expect(segments.find((s) => s.axis === 'cost')?.px).toBeCloseTo((0.3 / 1) * room, 9);
  });

  it('shares the room between stubs when every axis is one', () => {
    const weights = weightsOf(0.01, 0.005, 0.003, 0.002);
    const segments = layout(weights, AXES, 120);
    for (const segment of segments) expect(segment.stub).toBe(true);
    // not negative, not wider than the track, and the widths reflect the shares
    expect(sumOf(segments) + GAP_PX * 3).toBeCloseTo(120, 9);
    expect(segments[0].px).toBeGreaterThan(segments[3].px);
    expect(segments[3].px).toBeGreaterThan(0);
  });

  it('handles one axis, and no axes at all', () => {
    const one = layout({ quality: 1 }, ['quality'], 300);
    expect(one).toHaveLength(1);
    expect(one[0].px).toBeCloseTo(300, 9);
    expect(layout({}, AXES, 300)).toEqual([]);
  });

  it('draws the axes in the order the settings added them', () => {
    const weights = { cost: 0.5, quality: 0.5 };
    expect(layout(weights, ['quality', 'cost'], 100).map((s) => s.axis)).toEqual([
      'quality',
      'cost'
    ]);
    // an axis the weights dropped is not drawn, even if the order still holds it
    expect(layout({ quality: 1 }, ['quality', 'cost'], 100)).toHaveLength(1);
  });

  it('says which regime it is in, and keeps widths readable in both', () => {
    const weights = weightsOf(0.25, 0.25, 0.25, 0.25);
    expect(fits(weights, AXES, 400)).toBe(true);
    expect(fits(weights, AXES, 8)).toBe(false);
    expect(fits({}, AXES, 400)).toBe(false);
    // the same weights at each width: the sum only holds where it fits
    for (const barPx of [400, 8]) {
      for (const segment of layout(weights, AXES, barPx)) {
        expect(segment.px).toBeGreaterThanOrEqual(0);
      }
    }
  });

  it('crossing the stub threshold changes the mapping mid-drag', () => {
    const below = layout(weightsOf(0.5, 0.3, 0.18, 0.019), AXES, 400);
    const above = layout(weightsOf(0.5, 0.3, 0.18, 0.021), AXES, 400);
    const stub = below.find((segment) => segment.axis === 'latency');
    const proportional = above.find((segment) => segment.axis === 'latency');
    expect(stub?.stub).toBe(true);
    expect(proportional?.stub).toBe(false);
    // which is why a drag freezes its segments at pointer-down
    expect(stub?.px).not.toBeCloseTo(proportional?.px ?? 0, 3);
    expect(STUB_SHARE).toBe(0.02);
  });
});

describe('pxToShare', () => {
  it('round-trips with layout inside one regime', () => {
    const weights = weightsOf(0.5, 0.3, 0.15, 0.05);
    const barPx = 640;
    const segments = layout(weights, AXES, barPx);
    for (const axis of AXES) {
      const width = segments.find((segment) => segment.axis === axis)?.px ?? 0;
      expect(Math.abs(pxToShare(width, segments, barPx) - weights[axis])).toBeLessThan(1e-9);
    }
  });

  it('is worth nothing when there is no room to move', () => {
    const allStubs = layout(weightsOf(0.01, 0.01, 0.01, 0.01), AXES, 4);
    expect(pxToShare(10, allStubs, 4)).toBe(0);
    expect(pxToShare(Number.NaN, layout(weightsOf(0.25, 0.25, 0.25, 0.25), AXES, 400), 400)).toBe(0);
  });
});

describe('transfer', () => {
  it('conserves the pair, so the profile still sums to one', () => {
    const weights = { a: 0.6, b: 0.4 };
    const moved = transfer(weights, 'a', 'b', 0.25);
    expect(moved.a).toBeCloseTo(0.85, 12);
    expect(moved.b).toBeCloseTo(0.15, 12);
    expect(moved.a + moved.b).toBeCloseTo(1, 12);
    // and the input is untouched
    expect(weights).toEqual({ a: 0.6, b: 0.4 });
  });

  it('clamps to the pair total instead of going negative', () => {
    const weights = { a: 0.6, b: 0.4 };
    expect(transfer(weights, 'a', 'b', 5)).toEqual({ a: 1, b: 0 });
    expect(transfer(weights, 'a', 'b', -5)).toEqual({ a: 0, b: 1 });
  });

  it('moves nothing when the width it would need is zero', () => {
    expect(transfer({ a: 0, b: 1 }, 'a', 'b', -0.1)).toEqual({ a: 0, b: 1 });
    expect(transfer({ a: 1, b: 0 }, 'a', 'b', 0.1)).toEqual({ a: 1, b: 0 });
    expect(transfer({ a: 0.5, b: 0.5 }, 'a', 'b', 0)).toEqual({ a: 0.5, b: 0.5 });
    expect(transfer({ a: 0.5, b: 0.5 }, 'a', 'b', Number.NaN)).toEqual({ a: 0.5, b: 0.5 });
  });

  it('does nothing for an axis the weights do not hold', () => {
    expect(transfer({ a: 1 }, 'a', 'b', 0.1)).toEqual({ a: 1 });
  });
});

describe('parties', () => {
  const weights = weightsOf(0.4, 0.3, 0.2, 0.1);
  const segments = layout(weights, AXES, 400);

  it('names the two axes a divider sits between', () => {
    expect(parties(segments, [], 1)).toEqual(['quality', 'cost']);
    expect(parties(segments, [], 3)).toEqual(['agentic', 'latency']);
  });

  it('refuses a divider the track does not have', () => {
    expect(parties(segments, [], 0)).toBeNull();
    expect(parties(segments, [], 4)).toBeNull();
    expect(parties(segments, [], 1.5)).toBeNull();
  });

  it('refuses when either side is locked', () => {
    expect(parties(segments, ['quality'], 1)).toBeNull();
    expect(parties(segments, ['cost'], 1)).toBeNull();
    // and the next divider along is unaffected
    expect(parties(segments, ['quality'], 2)).toEqual(['cost', 'agentic']);
  });

  it('refuses a divider between two stubs', () => {
    const stubs = layout(weightsOf(0.4, 0.3, 0.01, 0.01), AXES, 400);
    expect(parties(stubs, [], 3)).toBeNull();
    expect(parties(stubs, [], 2)).toEqual(['cost', 'agentic']);
  });
});

describe('label', () => {
  it('drops what does not fit, rather than drawing it over its neighbour', () => {
    const wide: Segment = { axis: 'quality', weight: 0.45, px: 200, stub: false, color: 'c' };
    const middle: Segment = { ...wide, px: 60 };
    const narrow: Segment = { ...wide, px: 12 };
    expect(label(wide, 'Quality')).toEqual({ text: 'Quality', number: '45' });
    expect(label(middle, 'Quality')).toEqual({ text: '', number: '45' });
    expect(label(narrow, 'Quality')).toEqual({ text: '', number: '' });
  });

  it('rounds the share to the whole percent the bar is drawn at', () => {
    const segment: Segment = { axis: 'a', weight: 0.075, px: 200, stub: false, color: 'c' };
    expect(label(segment, 'a').number).toBe('8');
  });
});
