import { describe, expect, it } from 'vitest';
import { NEEDS } from '../../src/lib/api/client';
import type { Profile } from '../../src/lib/types';
import {
  addAxis,
  addManual,
  dropAxis,
  dropManual,
  fromServer,
  moveWeight,
  nudgeManual,
  pin,
  preset,
  remove,
  resetTo,
  restore,
  setExact,
  setMode,
  setShip,
  setTrim,
  toPatch,
  toggleLock,
  toggleNeed,
  transferWeight,
  type Refused,
  type Settings
} from '../../src/lib/console/logic/settings';
import { clampShip } from '../../src/lib/profile/tune';

/**
 * Every settings edit, over a random walk (CONSOLE.md section 5.2).
 *
 * Two invariants are load-bearing and are checked after *every* edit rather
 * than at the end, because the bugs that matter here are the ones an early edit
 * hides from a late assertion:
 *
 * - the weights are a distribution: all of them are there, none is negative,
 *   and they add up to 1;
 * - `order` and `locked` name axes the weights actually have, and no id is both
 *   pinned and removed.
 *
 * The walk is seeded, so a failure is reproducible from its seed and a fix
 * cannot pass by luck.
 */

const AXES = ['quality', 'cost', 'agentic', 'latency', 'context'];
const LOADED: Record<string, number> = {
  quality: 0.3,
  cost: 0.25,
  agentic: 0.2,
  latency: 0.15,
  context: 0.1
};
const IDS = ['openrouter/anthropic/claude-sonnet-4', 'openrouter/openai/gpt-5', 'local/qwen3-32b'];
const PREFIXES = ['openrouter', 'local'];

function profile(over: Partial<Profile> = {}): Profile {
  return {
    name: 'coder',
    modality: 'llm',
    purpose: 'code',
    weights: { ...LOADED },
    ship: 5,
    ...over
  };
}

function rngFrom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(rng: () => number, options: readonly T[]): T {
  return options[Math.floor(rng() * options.length) % options.length];
}

/** The invariants, in one place so every edit is held to all of them. */
function check(settings: Settings): void {
  const keys = Object.keys(settings.weights);
  expect(keys.length).toBeGreaterThan(0);
  for (const key of keys) expect(settings.weights[key]).toBeGreaterThanOrEqual(0);
  const sum = keys.reduce((total, key) => total + settings.weights[key], 0);
  expect(Math.abs(sum - 1)).toBeLessThan(1e-9);
  for (const axis of settings.order) expect(keys).toContain(axis);
  for (const key of keys) expect(settings.order).toContain(key);
  for (const axis of settings.locked) expect(keys).toContain(axis);
  for (const id of settings.pinned) expect(settings.removed).not.toContain(id);
  for (const need of settings.needs) expect(NEEDS).toContain(need);
  expect(settings.needs.length).toBe(new Set(settings.needs).size);
  expect(settings.manual.length).toBe(new Set(settings.manual).size);
  expect(settings.ship).toBeGreaterThanOrEqual(1);
}

function oneEdit(rng: () => number, settings: Settings): Settings {
  const axis = pick(rng, Object.keys(settings.weights));
  const id = pick(rng, IDS);
  switch (Math.floor(rng() * 16)) {
    case 0:
      return moveWeight(settings, axis, rng());
    case 1:
      return setExact(settings, axis, String(Math.floor(rng() * 101)));
    case 2:
      return toggleLock(settings, axis);
    case 3:
      return transferWeight(settings, axis, pick(rng, Object.keys(settings.weights)), rng() - 0.5);
    case 4:
      return preset(settings, pick(rng, ['even', 'quality', 'value'] as const));
    case 5:
      return addAxis(settings, pick(rng, [...AXES, 'fresh']));
    case 6: {
      const out = dropAxis(settings, axis);
      return 'error' in out ? settings : out;
    }
    case 7:
      return setShip(settings, Math.floor(rng() * 15) - 1);
    case 8:
      return setMode(settings, rng() < 0.5 ? 'auto' : 'manual', IDS);
    case 9:
      return toggleNeed(settings, pick(rng, NEEDS));
    case 10:
      return setTrim(settings, pick(rng, PREFIXES), pick(rng, ['', '0.5', '1', '2', 'x']));
    case 11:
      return pin(settings, id);
    case 12:
      return remove(settings, id);
    case 13:
      return restore(settings, id);
    case 14:
      return addManual(settings, id);
    case 15:
      return dropManual(settings, id);
    default:
      return nudgeManual(settings, id, Math.floor(rng() * 5) - 2);
  }
}

describe('the settings as a whole', () => {
  it(
    'keeps its invariants over a seeded random walk',
    () => {
      for (let seed = 1; seed <= 200; seed += 1) {
        const rng = rngFrom(seed);
        let settings = fromServer(profile(), null, []);
        check(settings);
        for (let step = 0; step < 60; step += 1) {
          settings = oneEdit(rng, settings);
          check(settings);
        }
      }
    },
    60_000
  );

  it('survives a reset in the middle of the walk', () => {
    for (let seed = 1; seed <= 10; seed += 1) {
      const rng = rngFrom(seed * 977);
      let settings = fromServer(profile(), null, []);
      for (let step = 0; step < 30; step += 1) {
        settings = oneEdit(rng, settings);
        if (step % 10 === 9) {
          settings = resetTo(settings, LOADED);
          check(settings);
        }
      }
    }
  });
});

describe('fromServer', () => {
  it('lets what the seat holds win over the profile defaults', () => {
    const settings = fromServer(profile(), { ship: 3, weights: { quality: 1 } }, []);
    expect(settings.weights).toEqual({ quality: 1 });
    expect(settings.order).toEqual(['quality']);
    expect(settings.ship).toBe(3);
  });

  it('drops a lock on an axis this profile does not score', () => {
    expect(fromServer(profile(), null, ['quality', 'gone']).locked).toEqual(['quality']);
  });

  it('reads the profile when the seat holds nothing yet', () => {
    const settings = fromServer(profile({ mode: 'manual', pinned: ['a'], needs: ['tools'] }), null, []);
    expect(settings.weights).toEqual(LOADED);
    expect(settings.mode).toBe('manual');
    expect(settings.pinned).toEqual(['a']);
    expect(settings.needs).toEqual(['tools']);
  });
});

describe('toPatch', () => {
  it('names every axis the settings no longer carry', () => {
    const settings = dropAxis(fromServer(profile(), null, []), 'cost') as Settings;
    const patch = toPatch(settings, AXES);
    expect(patch.remove_axes).toEqual(['cost']);
    expect(Object.keys(patch.weights ?? {})).not.toContain('cost');
    // the axes that stayed keep their proportions: 0.3 of the 0.75 left
    expect(patch.weights?.quality).toBeCloseTo(0.3 / 0.75, 6);
  });

  it('names nothing when every axis is still there', () => {
    expect(toPatch(fromServer(profile(), null, []), AXES).remove_axes).toEqual([]);
  });
});

describe('moveWeight', () => {
  it('keeps the weights a distribution when an axis is locked', () => {
    const start = fromServer(profile(), { ship: 5, weights: { a: 0.25, b: 0.25, c: 0.5 } }, ['c']);
    const moved = moveWeight(start, 'a', 0.9);
    // a is clamped to what c leaves, and b absorbs the difference
    expect(moved.weights.a).toBeCloseTo(0.5, 12);
    expect(moved.weights.c).toBeCloseTo(0.5, 12);
    expect(moved.weights.b).toBeCloseTo(0, 12);
    check(moved);
  });

  it('lets the moved axis move even when it is itself locked', () => {
    const start = fromServer(profile(), { ship: 5, weights: { a: 0.3, b: 0.3, c: 0.4 } }, ['a', 'c']);
    const moved = moveWeight(start, 'a', 0.5);
    expect(moved.weights.a).toBeCloseTo(0.5, 12);
    expect(moved.weights.b).toBeCloseTo(0.1, 12);
    check(moved);
  });

  it('refuses a move no free axis could absorb', () => {
    // b is locked and a is the only other axis: a cannot move at all without
    // the two of them adding to more than 1
    const start = fromServer(profile(), { ship: 5, weights: { a: 0.5, b: 0.5 } }, ['b']);
    expect(moveWeight(start, 'a', 0.2)).toBe(start);
    expect(moveWeight(start, 'a', 1)).toBe(start);
  });

  it('returns the same object when nothing changed', () => {
    const start = fromServer(profile(), null, []);
    expect(moveWeight(start, 'quality', start.weights.quality)).toBe(start);
    expect(moveWeight(start, 'quality', Number.NaN)).toBe(start);
    expect(moveWeight(start, 'quality', Number.POSITIVE_INFINITY)).toBe(start);
    expect(moveWeight(start, 'not-an-axis', 0.5)).toBe(start);
  });

  it('clamps a share into 0..1', () => {
    const start = fromServer(profile(), null, []);
    expect(moveWeight(start, 'quality', -5).weights.quality).toBeCloseTo(0, 12);
    expect(moveWeight(start, 'quality', 5).weights.quality).toBeCloseTo(1, 12);
    check(moveWeight(start, 'quality', 5));
  });
});

describe('setExact', () => {
  it('reads a typed percentage', () => {
    const start = fromServer(profile(), null, []);
    expect(setExact(start, 'quality', '40').weights.quality).toBeCloseTo(0.4, 12);
  });

  it('treats an unreadable entry as the value the control shows', () => {
    const start = fromServer(profile(), null, []);
    expect(setExact(start, 'quality', 'x')).toBe(start);
    // an empty field is what a number input sends for 0
    expect(setExact(start, 'quality', '').weights.quality).toBeCloseTo(0, 12);
  });
});

describe('transferWeight', () => {
  it('moves weight between two axes and conserves their total', () => {
    const start = fromServer(profile(), null, []);
    const moved = transferWeight(start, 'quality', 'cost', 0.1);
    expect(moved.weights.quality).toBeCloseTo(0.4, 12);
    expect(moved.weights.cost).toBeCloseTo(0.15, 12);
    check(moved);
  });

  it('stops at the end of the bar instead of going negative', () => {
    const start = fromServer(profile(), null, []);
    const moved = transferWeight(start, 'quality', 'cost', 5);
    expect(moved.weights.cost).toBeCloseTo(0, 12);
    check(moved);
  });

  it('has nothing to do for an axis the weights do not hold', () => {
    const start = fromServer(profile(), null, []);
    expect(transferWeight(start, 'quality', 'gone', 0.1)).toBe(start);
  });
});

describe('preset', () => {
  it('replaces the shares and leaves the axis order alone', () => {
    const start = fromServer(profile(), null, []);
    const even = preset(start, 'even');
    expect(even.order).toEqual(start.order);
    for (const axis of even.order) expect(even.weights[axis]).toBeCloseTo(0.2, 12);
    check(even);
  });

  it('returns the same object when the shares are already there', () => {
    const start = fromServer(profile(), null, []);
    const even = preset(start, 'even');
    expect(preset(even, 'even')).toBe(even);
  });
});

describe('addAxis and dropAxis', () => {
  it('adds an even share and keeps the total at one', () => {
    const start = fromServer(profile(), null, []);
    const added = addAxis(start, 'fresh');
    expect(added.order).toEqual([...AXES, 'fresh']);
    expect(added.weights.fresh).toBeCloseTo(1 / 6, 12);
    check(added);
  });

  it('does not add an axis twice', () => {
    const start = fromServer(profile(), null, []);
    const once = addAxis(start, 'fresh');
    expect(addAxis(once, 'fresh')).toBe(once);
  });

  it('refuses to remove the last axis', () => {
    const single = fromServer(profile({ weights: { only: 1 } }), null, []);
    const refused = dropAxis(single, 'only') as Refused;
    expect(refused.error).toMatch(/at least one axis/);
  });

  it('removes an axis from the weights, the order and the locks', () => {
    const start = fromServer(profile(), null, ['cost']);
    const dropped = dropAxis(start, 'cost') as Settings;
    expect(dropped.weights).not.toHaveProperty('cost');
    expect(dropped.order).not.toContain('cost');
    expect(dropped.locked).not.toContain('cost');
    check(dropped);
  });

  it('has nothing to do for an axis that is not there', () => {
    const start = fromServer(profile(), null, []);
    expect(dropAxis(start, 'gone')).toBe(start);
  });
});

describe('resetTo', () => {
  it('puts the loaded weights back and forgets what was added', () => {
    const start = addAxis(fromServer(profile(), null, ['cost']), 'fresh');
    const reset = resetTo(start, LOADED);
    expect(reset.weights).toEqual(LOADED);
    expect(reset.order).toEqual(Object.keys(LOADED));
    expect(reset.locked).toEqual(['cost']);
    check(reset);
  });

  it('is weights-only: everything else survives it', () => {
    const start = setShip(setMode(pin(fromServer(profile(), null, []), 'a'), 'manual', IDS), 3);
    const reset = resetTo(start, LOADED);
    expect(reset.ship).toBe(3);
    expect(reset.mode).toBe('manual');
    expect(reset.pinned).toEqual(['a']);
  });

  it('returns the same object when the weights are already loaded', () => {
    const start = fromServer(profile(), null, []);
    expect(resetTo(start, LOADED)).toBe(start);
  });
});

describe('the simple fields', () => {
  it('clamps the ship count', () => {
    const start = fromServer(profile(), null, []);
    expect(setShip(start, 0).ship).toBe(clampShip(0));
    expect(setShip(start, 99).ship).toBe(clampShip(99));
    expect(setShip(start, start.ship)).toBe(start);
  });

  it('starts a hand-made list from what ships now', () => {
    const start = fromServer(profile(), null, []);
    const manual = setMode(start, 'manual', IDS);
    expect(manual.mode).toBe('manual');
    expect(manual.manual).toEqual(IDS);
    expect(setMode(manual, 'auto', IDS).manual).toEqual(IDS);
    expect(setMode(manual, 'manual', [])).toBe(manual);
  });

  it('toggles a need', () => {
    const start = fromServer(profile(), null, []);
    expect(toggleNeed(start, 'vision').needs).toEqual(['vision']);
    expect(toggleNeed(toggleNeed(start, 'vision'), 'vision').needs).toEqual([]);
  });

  it('writes a trim factor, and forgets one that says nothing', () => {
    const start = fromServer(profile(), null, []);
    expect(setTrim(start, 'openrouter', '0.8').prefixWeights).toEqual({ openrouter: 0.8 });
    const trimmed = setTrim(start, 'openrouter', '0.8');
    expect(setTrim(trimmed, 'openrouter', '1').prefixWeights).toEqual({});
    expect(setTrim(trimmed, 'openrouter', '').prefixWeights).toEqual({});
    expect(setTrim(trimmed, 'openrouter', 'x').prefixWeights).toEqual({});
    expect(setTrim(trimmed, 'openrouter', '-2').prefixWeights).toEqual({});
    expect(setTrim(start, 'openrouter', '1')).toBe(start);
  });

  it('keeps pins and removals exclusive, and forgets neither the other way', () => {
    const start = fromServer(profile(), null, []);
    const pinned = pin(start, 'a');
    expect(pinned.pinned).toEqual(['a']);
    const removed = remove(pinned, 'a');
    expect(removed.pinned).toEqual([]);
    expect(removed.removed).toEqual(['a']);
    expect(pin(removed, 'a').removed).toEqual([]);
    expect(restore(removed, 'a').removed).toEqual([]);
    expect(restore(start, 'nothing')).toBe(start);
    expect(remove(start, 'b').removed).toEqual(['b']);
    expect(pin(start, 'b').pinned).toEqual(['b']);
  });

  it('edits the hand-made list without reordering it by accident', () => {
    const start = setMode(fromServer(profile(), null, []), 'manual', IDS);
    expect(addManual(start, 'x').manual).toEqual([...IDS, 'x']);
    expect(addManual(start, IDS[0])).toBe(start);
    expect(dropManual(start, IDS[0]).manual).toEqual(IDS.slice(1));
    expect(dropManual(start, 'x')).toBe(start);
    expect(nudgeManual(start, IDS[1], -1).manual).toEqual([IDS[1], IDS[0], IDS[2]]);
    expect(nudgeManual(start, IDS[0], 1).manual).toEqual([IDS[1], IDS[0], IDS[2]]);
    expect(nudgeManual(start, IDS[0], -1)).toBe(start);
    expect(nudgeManual(start, 'x', 1)).toBe(start);
  });
});
