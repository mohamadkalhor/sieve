/**
 * The weight bar's geometry (CONSOLE.md section 5.2, REVIEW.md finding 7).
 *
 * The rule the whole file exists for, on the weight track only -- the Add
 * button sits outside it and is not counted, and neither is any padding the
 * page draws:
 *
 *     sum(segment.px) + GAP_PX * (n - 1) === barPx
 *
 * Widths are floating point, so a fifth axis does not blunt the bar by a
 * rounding error, and they are never negative.
 *
 * Two regimes, one switch. A very small share is a *stub*: it is drawn at
 * `STUB_PX` so it stays draggable and visible, and the proportional axes share
 * what is left. That means a share crossing `STUB_SHARE` moves every other
 * pixel on the bar, which is why a drag freezes its parties, its segments and
 * its scale at pointer-down and computes every move from total displacement
 * against that snapshot -- otherwise the mapping would change under the
 * pointer. When even the stubs and gaps do not fit, `layout` keeps handing back
 * non-negative widths (so nothing renders inside out) and `fits` says false, at
 * which point the caller renders the exact-percent list instead of the bar and
 * disables dragging. In that regime the sums above do not hold and are not
 * claimed.
 *
 * Axis colours come from the token layer by name, `--c-axis-1` and on, in track
 * order; `layout` knows nothing about which axes a profile has, so a component
 * can render a segment list without re-deriving anything.
 */
export interface Segment {
  axis: string;
  /** the share of the bar's weight this axis holds, 0..1 */
  weight: number;
  /** its width in pixels, never negative */
  px: number;
  /** drawn at a minimum width rather than in proportion */
  stub: boolean;
  /** the CSS colour token for this axis, by its position on the track */
  color: string;
}

/** The narrowest a draggable segment may be drawn. */
export const STUB_PX = 14;

/** A share below this is drawn as a stub rather than in proportion. */
export const STUB_SHARE = 0.02;

/** The gap drawn between two segments, and inside the track's width. */
export const GAP_PX = 3;

/** One arrow press on a focused divider. Shift multiplies it by `KEY_STEP_BIG`. */
export const KEY_STEP = 0.01;
export const KEY_STEP_BIG = 0.05;

/** Above `FULL_PX` a segment shows its short axis label and its number; above
 *  `NUMBER_PX` it shows the number alone; below that, neither fits. */
export const FULL_PX = 96;
export const NUMBER_PX = 40;

function axisColor(index: number): string {
  return `var(--c-axis-${index % 8})`;
}

/** The axes a track draws: the weights in their added order, never re-sorted. */
export function trackAxes(
  weights: Record<string, number>,
  order: readonly string[]
): string[] {
  return order.filter((axis) => axis in weights);
}

function isStub(weight: number): boolean {
  return weight < STUB_SHARE;
}

/**
 * Whether the bar can be drawn honestly at this width: every stub at its
 * minimum, every gap, and something left for the proportional axes.
 *
 * False is the caller's cue to render the exact-percent list instead, with
 * dragging disabled.
 */
export function fits(
  weights: Record<string, number>,
  order: readonly string[],
  barPx: number
): boolean {
  const axes = trackAxes(weights, order);
  if (!axes.length || !Number.isFinite(barPx)) return false;
  const stubs = axes.filter((axis) => isStub(weights[axis])).length;
  return barPx >= stubs * STUB_PX + GAP_PX * (axes.length - 1);
}

/**
 * The segments, in order, for a track `barPx` wide.
 *
 * Deterministic in `(weights, order, barPx)`: the same inputs give the same
 * widths, so the round-trip with `pxToShare` holds inside one regime.
 */
export function layout(
  weights: Record<string, number>,
  order: readonly string[],
  barPx: number
): Segment[] {
  const axes = trackAxes(weights, order);
  const gaps = GAP_PX * Math.max(0, axes.length - 1);
  const width = Number.isFinite(barPx) && barPx > 0 ? barPx : 0;

  if (!axes.length) return [];

  // Does not fit: keep the proportions (they are still the truth about the
  // weights) inside whatever width there is, and let the caller switch the
  // presentation. Nothing here is negative and nothing overflows.
  if (!fits(weights, order, width)) {
    const room = Math.max(0, width - gaps);
    return axes.map((axis, index) => ({
      axis,
      weight: weights[axis],
      px: room * weights[axis],
      stub: isStub(weights[axis]),
      color: axisColor(index)
    }));
  }

  const segments: Segment[] = axes.map((axis, index) => ({
    axis,
    weight: weights[axis],
    px: isStub(weights[axis]) ? STUB_PX : 0,
    stub: isStub(weights[axis]),
    color: axisColor(index)
  }));

  const proportional = segments.filter((segment) => !segment.stub);
  const stubs = segments.filter((segment) => segment.stub).length;
  const room = Math.max(0, width - gaps - stubs * STUB_PX);

  if (proportional.length) {
    const total = proportional.reduce((sum, segment) => sum + segment.weight, 0);
    for (const segment of proportional) {
      segment.px = total > 0 ? (segment.weight / total) * room : room / proportional.length;
    }
  } else {
    // every axis is a stub: they still share the room in proportion, so the bar
    // keeps saying which of them is bigger
    const total = segments.reduce((sum, segment) => sum + segment.weight, 0);
    for (const segment of segments) {
      segment.px = STUB_PX + (total > 0 ? (segment.weight / total) * room : room / segments.length);
    }
  }

  // Put back what floating point lost, on the widest segment, so the track is
  // exactly barPx wide -- the sum the acceptance test asserts.
  const drift = width - gaps - segments.reduce((sum, segment) => sum + segment.px, 0);
  if (Math.abs(drift) > 1e-12) {
    const widest = segments.reduce((a, b) => (a.px >= b.px ? a : b));
    widest.px += drift;
  }
  return segments;
}

/**
 * How much of a weight a drag of `deltaPx` is worth, inside one laid-out track.
 *
 * This is the inverse of `layout` for the proportional axes, so:
 * dragging a divider by the pixels its neighbour occupies moves that whole
 * share. Stubs and gaps are not movable, which is why they come out of the
 * room. Zero room means nothing to move.
 */
export function pxToShare(deltaPx: number, segments: readonly Segment[], barPx: number): number {
  const gaps = GAP_PX * Math.max(0, segments.length - 1);
  const stubs = segments.filter((segment) => segment.stub).length;
  const room = barPx - stubs * STUB_PX - gaps;
  if (!Number.isFinite(deltaPx) || room <= 0) return 0;
  const movable = segments
    .filter((segment) => !segment.stub)
    .reduce((sum, segment) => sum + segment.weight, 0);
  return (deltaPx / room) * movable;
}

/**
 * Move weight from one axis to another, clamped to the pair's own total.
 *
 * The pair's sum is conserved exactly, which is what keeps the profile's total
 * at 1; a drag past either end of the pair stops rather than going negative,
 * and zero movable width moves nothing.
 */
export function transfer(
  weights: Record<string, number>,
  left: string,
  right: string,
  delta: number
): Record<string, number> {
  // one axis against itself is not a transfer: writing both keys would leave
  // the vector short by `delta`
  if (left === right) return { ...weights };
  if (!(left in weights) || !(right in weights)) return { ...weights };
  if (!Number.isFinite(delta) || delta === 0) return { ...weights };
  const moved = Math.max(-weights[left], Math.min(weights[right], delta));
  if (moved === 0) return { ...weights };
  return { ...weights, [left]: weights[left] + moved, [right]: weights[right] - moved };
}

/**
 * The axes a divider at `at` (between index `at - 1` and `at`) may drag both
 * sides of. Locked axes are not movable, and the pair may not be a stub
 * against a stub. Empty means the divider does not drag.
 */
export function parties(
  segments: readonly Segment[],
  locked: readonly string[],
  at: number
): [string, string] | null {
  if (!Number.isInteger(at) || at <= 0 || at >= segments.length) return null;
  const left = segments[at - 1];
  const right = segments[at];
  if (!left || !right) return null;
  if (locked.includes(left.axis) || locked.includes(right.axis)) return null;
  if (left.stub && right.stub) return null;
  return [left.axis, right.axis];
}

/**
 * What a segment has room to say at its current width.
 *
 * The number is the rounded whole percent, which is what the bar is: a picture,
 * not a measurement. The exact share lives in the divider's title, its
 * `aria-label`, and the exact-percent input, and those are the numbers a person
 * acts on.
 */
export function label(segment: Segment, axisLabel: string): { text: string; number: string } {
  const number = `${Math.round(segment.weight * 100)}`;
  if (segment.px >= FULL_PX) return { text: axisLabel, number };
  if (segment.px >= NUMBER_PX) return { text: '', number };
  return { text: '', number: '' };
}
