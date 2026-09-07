/**
 * The scoring rule, in the browser (CONTRACTS section 8).
 *
 * A port of `sieve/scoring/weigh.py`, and it must agree with it to 1e-6 -- a
 * vitest here and a pytest there both assert against
 * `tests/fixtures/rank_case.json`. This is what makes a weight slider feel
 * instant: the list re-ranks locally on every input, and `Evaluate` asks the
 * server only when the user wants the answer of record.
 *
 * The rules, restated because they are easy to get subtly wrong:
 *   - score      = sum over weighted axes of weight * value
 *   - confidence = sum over weighted axes of weight * coverage
 *   - an unmeasured axis (value null) contributes 0 to both -- never a silent
 *     zero, because the loss is visible in `confidence`
 *   - axes outside `weights` are ignored entirely
 *   - an exact tie is broken by model id, ascending
 */

export interface AxisValue {
  value: number | null;
  coverage: number;
}

export type AxesByModel = Record<string, Record<string, AxisValue>>;

export interface Scored {
  model_id: string;
  score: number;
  confidence: number;
  contributions: Record<string, number>;
}

export function weighOne(
  axes: Record<string, AxisValue>,
  weights: Record<string, number>
): { score: number; confidence: number; contributions: Record<string, number> } {
  const contributions: Record<string, number> = {};
  let score = 0;
  let confidence = 0;

  for (const [axis, weight] of Object.entries(weights)) {
    const held = axes[axis];
    const measured = held !== undefined && held.value !== null;
    const contribution = weight * (measured ? (held.value as number) : 0);
    contributions[axis] = contribution;
    score += contribution;
    confidence += weight * (measured ? held.coverage : 0);
  }

  return { score, confidence, contributions };
}

export function weigh(axesByModel: AxesByModel, weights: Record<string, number>): Scored[] {
  return Object.entries(axesByModel).map(([model_id, axes]) => ({
    model_id,
    ...weighOne(axes, weights)
  }));
}

/** Best first; an exact tie ranks by model id, ascending. */
export function rankOrder(scored: Scored[]): Scored[] {
  return [...scored].sort((a, b) =>
    a.score === b.score ? a.model_id.localeCompare(b.model_id) : b.score - a.score
  );
}

/** Best first, with anything under the confidence floor pushed to the end. */
export function rankWithFloor(scored: Scored[], minConfidence: number): Scored[] {
  const kept = scored.filter((row) => row.confidence >= minConfidence);
  const dropped = scored.filter((row) => row.confidence < minConfidence);
  return [...rankOrder(kept), ...rankOrder(dropped)];
}

/**
 * Keep a weight vector summing to 1 while one slider moves.
 *
 * The others take the remainder in proportion to what they already had, so
 * dragging one axis does not silently reorder the rest. Locked axes hold their
 * value and only the unlocked ones absorb the change.
 */
export function renormalise(
  weights: Record<string, number>,
  moved: string,
  value: number,
  locked: ReadonlySet<string> = new Set()
): Record<string, number> {
  const clamped = Math.min(1, Math.max(0, value));
  const held = Object.entries(weights).filter(([axis]) => axis !== moved && locked.has(axis));
  const free = Object.entries(weights).filter(([axis]) => axis !== moved && !locked.has(axis));

  const heldTotal = held.reduce((sum, [, w]) => sum + w, 0);
  const room = Math.max(0, 1 - clamped - heldTotal);
  const freeTotal = free.reduce((sum, [, w]) => sum + w, 0);

  const out: Record<string, number> = { [moved]: clamped };
  for (const [axis, weight] of held) out[axis] = weight;
  for (const [axis, weight] of free) {
    out[axis] = freeTotal > 0 ? (weight / freeTotal) * room : room / Math.max(1, free.length);
  }

  // put back what rounding lost, on the largest free axis, so the sum is 1
  const total = Object.values(out).reduce((sum, w) => sum + w, 0);
  const drift = 1 - total;
  if (Math.abs(drift) > 1e-12 && free.length) {
    const biggest = free.reduce((a, b) => (out[a[0]] >= out[b[0]] ? a : b))[0];
    out[biggest] += drift;
  }
  return out;
}

/** The axis carrying most of the gap between two models, for the "why" line. */
export function carriedBy(
  leader: Record<string, number>,
  runner: Record<string, number>
): { axis: string; delta: number } | null {
  const gaps = Object.entries(leader)
    .map(([axis, value]) => ({ axis, delta: value - (runner[axis] ?? 0) }))
    .filter((row) => row.delta > 0)
    .sort((a, b) => b.delta - a.delta);
  return gaps[0] ?? null;
}
