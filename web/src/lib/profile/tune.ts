/**
 * Tuning a profile: the arithmetic and the honest states.
 *
 * All of it is here, outside the component, because it is the part that has to
 * be right rather than the part that has to look right: the shares always add
 * to one, a list that is still being computed never looks like a list that came
 * back empty, and a button that would do nothing says so before it is pressed.
 */
import { renormalise } from '$lib/rank/weigh';

export { renormalise };

/** How many models a profile may ship. The server agrees, and says so. */
export const SHIP_MIN = 1;
export const SHIP_MAX = 10;

/** The preview is asked for this long after the last movement of a slider. */
export const DEBOUNCE_MS = 400;

/** After this long with no answer, the box is busy and the page says so. */
export const SLOW_MS = 8000;

/**
 * Add an axis: it takes an even share, and the others are renormalised.
 *
 * "Even" means 1/n over the axes there will be, which is the only starting
 * value that carries no opinion. Everything else keeps its proportion, so
 * adding a fifth axis to four does not silently reorder the first four.
 */
export function addAxis(weights: Record<string, number>, axis: string): Record<string, number> {
  if (axis in weights) return { ...weights };
  const share = 1 / (Object.keys(weights).length + 1);
  return renormalise({ ...weights, [axis]: share }, axis, share);
}

/**
 * Remove an axis: what is left is renormalised, keeping its proportions.
 *
 * Removing the last one leaves nothing, which the server refuses -- a profile
 * is its weights -- so the page keeps the last row.
 */
export function removeAxis(weights: Record<string, number>, axis: string): Record<string, number> {
  const rest = Object.entries(weights).filter(([name]) => name !== axis);
  if (!rest.length) return { ...weights };
  const total = rest.reduce((sum, [, weight]) => sum + weight, 0);
  const out: Record<string, number> = {};
  for (const [name, weight] of rest) {
    out[name] = total > 0 ? weight / total : 1 / rest.length;
  }
  // put back what rounding lost, on the largest share, so the sum is 1
  const drift = 1 - Object.values(out).reduce((sum, weight) => sum + weight, 0);
  if (Math.abs(drift) > 1e-12) {
    const biggest = Object.keys(out).reduce((a, b) => (out[a] >= out[b] ? a : b));
    out[biggest] += drift;
  }
  return out;
}

/** One weight moved; every other share follows it. */
export function moveAxis(
  weights: Record<string, number>,
  axis: string,
  value: number
): Record<string, number> {
  return renormalise(weights, axis, value);
}

export function clampShip(value: number): number {
  if (!Number.isFinite(value)) return SHIP_MIN;
  return Math.max(SHIP_MIN, Math.min(SHIP_MAX, Math.round(value)));
}

/**
 * What the list panel is doing, in the only four words it may say.
 *
 * The distinction that matters is the last one: "nothing ships" is a fact
 * about the models, and it may only be shown when an answer actually came
 * back. A page that says it while a request is in flight -- or after one
 * failed -- is reporting an absence as a fact.
 */
export type ListState = 'ranking' | 'busy' | 'error' | 'empty' | 'ready';

export interface ListView {
  /** a request is in flight */
  pending: boolean;
  /** how long it has been in flight */
  waitedMs: number;
  /** what the last request failed with, if it failed */
  error: string | null;
  /** the models the last answer held, or null if there has been no answer */
  models: unknown[] | null;
}

export function listState({ pending, waitedMs, error, models }: ListView): ListState {
  if (pending) return waitedMs >= SLOW_MS ? 'busy' : 'ranking';
  if (error) return 'error';
  if (models === null) return 'ranking';
  return models.length ? 'ready' : 'empty';
}

/** Whether the state is one a person can do something about by trying again. */
export function retryable(state: ListState): boolean {
  return state === 'busy' || state === 'error';
}

export interface ShipView {
  /** the ids this profile is shipping right now, in order */
  shipped: string[];
  /** the ids these weights would ship, in order */
  next: string[];
  /** a ship is already going out */
  shipping: boolean;
  /** the list panel has an answer to ship */
  ready: boolean;
}

export interface ShipState {
  disabled: boolean;
  /** why it is disabled, for the button's title. Empty when it is not. */
  title: string;
  label: string;
}

/**
 * The Ship now button: pressable only when pressing it would change something.
 *
 * A button that is always pressable teaches nobody anything; one that says
 * "this is already what ships" answers the question the person was about to
 * ask, which is whether their change is live.
 */
export function shipState({ shipped, next, shipping, ready }: ShipView): ShipState {
  if (shipping) return { disabled: true, title: 'shipping…', label: 'Shipping…' };
  if (!ready) return { disabled: true, title: 'waiting for the list', label: 'Ship now' };
  if (!next.length) return { disabled: true, title: 'there is nothing to ship', label: 'Ship now' };
  if (same(shipped, next)) {
    return { disabled: true, title: 'this is already what ships', label: 'Ship now' };
  }
  return { disabled: false, title: '', label: 'Ship now' };
}

export function same(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((id, i) => id === right[i]);
}

/** A score as a bar width: 0..1 of the panel, never past either end. */
export function barWidth(score: number): string {
  return `${Math.max(0, Math.min(1, score)) * 100}%`;
}
