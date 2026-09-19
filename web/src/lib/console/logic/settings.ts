/**
 * A profile's settings, and every edit that may be made to them (CONSOLE.md
 * section 5.2).
 *
 * This is the old profile page's `move`/`exact`/`preset`/`drop`/`pin` block,
 * lifted out of the component and made total: every function takes the
 * settings and gives settings back, so the seat store holds one value and the
 * inspector, the split bar and the keyboard all go through the same arithmetic.
 *
 * Two rules run through all of it.
 *
 * **The weights always add to one.** The shares are a distribution, not a
 * score, and a distribution that sums to 0.94 is a bug that shows up three
 * screens away. Every move therefore ends by renormalising, and `moveWeight`
 * refuses a value the locked axes leave no room for rather than writing a
 * vector the server will reject.
 *
 * **Nothing changed means the same object comes back.** A store can compare by
 * identity and skip a preview request; a function that returned a fresh copy
 * of an unchanged vector would make every no-op edit look like a change.
 *
 * Locked axes are page-only state (they live in `localStorage`, per seat) and
 * never travel in a patch, which is why `toPatch` does not mention them.
 */
import type { Need, ProfileMode, ProfileSettings, SettingsPatch } from '$lib/api/client';
import type { Profile } from '$lib/types';
import {
  addAxis as addAxisTo,
  applyPreset,
  clampShip,
  removeAxis,
  renormalise,
  shift,
  toggle,
  type Preset
} from '$lib/profile/tune';
import { transfer } from './split';

/** How much of a weight a comparison may miss before it counts as a change. */
const EPS = 1e-12;

/** The settings a seat is edited through: the server's fields plus the locks. */
export interface Settings {
  weights: Record<string, number>;
  /** axis order as added; never re-sorted by weight */
  order: string[];
  /** page-only locks, persisted per seat in localStorage */
  locked: string[];
  ship: number;
  mode: ProfileMode;
  manual: string[];
  pinned: string[];
  removed: string[];
  needs: Need[];
  prefixWeights: Record<string, number>;
}

/** The failure `dropAxis` may hand back, as `session.edit` already takes it. */
export interface Refused {
  error: string;
}

function sameRecord(left: Record<string, number>, right: Record<string, number>): boolean {
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every(
    (key) => key in right && Math.abs(left[key] - right[key]) <= EPS
  );
}

function sameList(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

/**
 * What the server says, plus the locks this browser remembers.
 *
 * `held` is the settings the seat actually carries (the preview's `settings`,
 * or the saved ones) and wins over the profile's own defaults wherever the
 * server answered. Locks that name an axis this profile no longer scores are
 * dropped: a lock on an axis that is not there would be invisible state that
 * changes what the next edit does.
 */
export function fromServer(
  profile: Profile,
  held: ProfileSettings | null,
  locks: readonly string[]
): Settings {
  const weights = held?.weights ? { ...held.weights } : { ...profile.weights };
  return {
    weights,
    order: Object.keys(weights),
    locked: locks.filter((axis) => axis in weights),
    ship: clampShip(held ? held.ship : (profile.ship ?? 4)),
    mode: held?.mode ?? profile.mode ?? 'auto',
    manual: [...(held?.manual ?? profile.manual ?? [])],
    pinned: [...(held?.pinned ?? profile.pinned ?? [])],
    removed: [...(held?.removed ?? profile.removed ?? [])],
    needs: [...(held?.needs ?? profile.needs ?? [])],
    prefixWeights: { ...(held?.prefix_weights ?? profile.prefix_weights ?? {}) }
  };
}

/**
 * What a page sends: every field it holds, plus the axes it wants taken off.
 *
 * `everyAxis` is the profile's full axis list; an axis the settings no longer
 * carry is named in `remove_axes`, which is how an axis is dropped for good.
 */
export function toPatch(settings: Settings, everyAxis: readonly string[]): SettingsPatch {
  return {
    weights: { ...settings.weights },
    ship: settings.ship,
    mode: settings.mode,
    manual: [...settings.manual],
    pinned: [...settings.pinned],
    removed: [...settings.removed],
    needs: [...settings.needs],
    prefix_weights: { ...settings.prefixWeights },
    remove_axes: everyAxis.filter((axis) => !(axis in settings.weights))
  };
}

/**
 * Move one weight; the axes that are free absorb the difference.
 *
 * The value is clamped to the room the *other* locked axes leave, because the
 * shared `renormalise` helper clamps to 1: with one axis locked at 0.5 and the
 * other one free, asking for 0.8 would otherwise write 1.3 of weights. When no
 * other axis is free the vector can only stay as it is -- the moved axis is
 * forced to `1 - sum(other locked)` and nothing can hold that value down -- so
 * the edit is refused by returning the settings unchanged.
 */
export function moveWeight(settings: Settings, axis: string, value: number): Settings {
  if (!(axis in settings.weights) || !Number.isFinite(value)) return settings;

  const others = settings.locked.filter((held) => held !== axis);
  const free = Object.keys(settings.weights).filter(
    (held) => held !== axis && !settings.locked.includes(held)
  );
  const room = 1 - others.reduce((sum, held) => sum + settings.weights[held], 0);
  if (!free.length || room <= EPS) return settings;

  const clamped = Math.min(Math.max(value, 0), room);
  const weights = renormalise(settings.weights, axis, clamped, new Set(others));
  return sameRecord(weights, settings.weights) ? settings : { ...settings, weights };
}

/** A percentage typed into the exact control, as a weight. */
export function setExact(settings: Settings, axis: string, percentText: string): Settings {
  const value = Number(percentText);
  if (!Number.isFinite(value)) return settings;
  return moveWeight(settings, axis, value / 100);
}

/**
 * A drag on the divider between two axes: the pair keeps its total.
 *
 * The caller converts pixels to shares (`split.pxToShare`) and the clamping
 * against the pair total happens in `split.transfer`, so a drag past the end of
 * the bar stops instead of pushing one axis below zero.
 */
export function transferWeight(
  settings: Settings,
  left: string,
  right: string,
  delta: number
): Settings {
  if (left === right) return settings;
  if (!(left in settings.weights) || !(right in settings.weights)) return settings;
  const weights = transfer(settings.weights, left, right, delta);
  return sameRecord(weights, settings.weights) ? settings : { ...settings, weights };
}

/** Locking is page-only, and only an axis that is scored can be locked. */
export function toggleLock(settings: Settings, axis: string): Settings {
  if (!(axis in settings.weights)) return settings;
  return { ...settings, locked: toggle(settings.locked, axis) };
}

/** A one-press starting point. The axis order is not touched. */
export function preset(settings: Settings, kind: Preset): Settings {
  const weights = applyPreset(settings.weights, kind);
  return sameRecord(weights, settings.weights) ? settings : { ...settings, weights };
}

/**
 * Reset: the weights as they were loaded, and only those.
 *
 * The order is rebuilt from the loaded keys -- a key that was added and then
 * reset has to leave the order too -- and locks that name an axis the loaded
 * weights do not have are dropped, for the same reason `fromServer` drops them.
 * Reset is weights-only on purpose (REVIEW.md finding 12): mode, ship, needs,
 * manual, pins, removals and trim survive it.
 */
export function resetTo(settings: Settings, loaded: Record<string, number>): Settings {
  const weights = { ...loaded };
  const order = Object.keys(loaded);
  const locked = settings.locked.filter((axis) => axis in weights);
  if (
    sameRecord(weights, settings.weights) &&
    sameList(order, settings.order) &&
    sameList(locked, settings.locked)
  ) {
    return settings;
  }
  return { ...settings, weights, order, locked };
}

/** Add an axis: an even share to the newcomer, the rest keep their proportion. */
export function addAxis(settings: Settings, axis: string): Settings {
  const weights = addAxisTo(settings.weights, axis);
  const order = [...settings.order.filter((held) => held !== axis), axis];
  if (sameRecord(weights, settings.weights) && sameList(order, settings.order)) return settings;
  return { ...settings, weights, order };
}

/**
 * Remove an axis. The last one is refused: a profile is its weights, and the
 * server will not take a vector with nothing in it.
 */
export function dropAxis(settings: Settings, axis: string): Settings | Refused {
  if (!(axis in settings.weights)) return settings;
  if (Object.keys(settings.weights).length <= 1) {
    return { error: 'A profile needs at least one axis to score by.' };
  }
  return {
    ...settings,
    weights: removeAxis(settings.weights, axis),
    order: settings.order.filter((held) => held !== axis),
    locked: settings.locked.filter((held) => held !== axis)
  };
}

export function setShip(settings: Settings, value: number): Settings {
  const ship = clampShip(value);
  return ship === settings.ship ? settings : { ...settings, ship };
}

/**
 * Auto or hand-made. Switching to manual for the first time starts from what
 * ships now rather than from nothing, so the list opens as the seat's current
 * answer instead of an empty page.
 */
export function setMode(
  settings: Settings,
  mode: ProfileMode,
  currentLineupIds: readonly string[]
): Settings {
  if (mode === settings.mode) return settings;
  const manual =
    mode === 'manual' && settings.manual.length === 0 && currentLineupIds.length
      ? [...currentLineupIds]
      : settings.manual;
  return { ...settings, mode, manual };
}

/** A must-support need, on or off. */
export function toggleNeed(settings: Settings, need: Need): Settings {
  return { ...settings, needs: toggle(settings.needs, need) as Need[] };
}

/**
 * A router prefix's trim factor.
 *
 * Blank, unreadable, negative, or exactly 1 all mean the same thing: no trim.
 * 1 is not an opinion about a prefix, it is the absence of one, and writing it
 * down would leave a row in the trim list that says nothing.
 */
export function setTrim(settings: Settings, prefix: string, rawText: string): Settings {
  const value = Number(rawText);
  const rest = Object.fromEntries(
    Object.entries(settings.prefixWeights).filter(([key]) => key !== prefix)
  );
  const prefixWeights =
    rawText.trim() === '' || !Number.isFinite(value) || value < 0 || value === 1
      ? rest
      : { ...rest, [prefix]: value };
  return sameRecord(prefixWeights, settings.prefixWeights) ? settings : { ...settings, prefixWeights };
}

/** Pin or unpin. Pinning takes an id off the never-ship list: the two are one
 *  question, and an id on both is a state the server has to guess about. */
export function pin(settings: Settings, id: string): Settings {
  const pinned = toggle(settings.pinned, id);
  const removed = settings.removed.filter((held) => held !== id);
  if (sameList(pinned, settings.pinned) && sameList(removed, settings.removed)) return settings;
  return { ...settings, pinned, removed };
}

/** Never ship. Removing an id takes it off the pinned list, as above. */
export function remove(settings: Settings, id: string): Settings {
  const removed = settings.removed.includes(id) ? settings.removed : [...settings.removed, id];
  const pinned = settings.pinned.filter((held) => held !== id);
  if (sameList(removed, settings.removed) && sameList(pinned, settings.pinned)) return settings;
  return { ...settings, removed, pinned };
}

/** Put a removed id back in play. */
export function restore(settings: Settings, id: string): Settings {
  const removed = settings.removed.filter((held) => held !== id);
  return sameList(removed, settings.removed) ? settings : { ...settings, removed };
}

/** Add to the hand-made list. */
export function addManual(settings: Settings, id: string): Settings {
  return settings.manual.includes(id) ? settings : { ...settings, manual: [...settings.manual, id] };
}

/** Take off the hand-made list. */
export function dropManual(settings: Settings, id: string): Settings {
  const manual = settings.manual.filter((held) => held !== id);
  return sameList(manual, settings.manual) ? settings : { ...settings, manual };
}

/** Move one entry of the hand-made list, clamped at both ends. */
export function nudgeManual(settings: Settings, id: string, by: number): Settings {
  const index = settings.manual.indexOf(id);
  if (index < 0) return settings;
  const manual = shift(settings.manual, index, by);
  return sameList(manual, settings.manual) ? settings : { ...settings, manual };
}
