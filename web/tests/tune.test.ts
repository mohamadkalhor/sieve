/**
 * The profile page, tested where it can be wrong.
 *
 * The three things a person would notice immediately if they broke: the shares
 * always add to one however a slider is dragged, the list never claims to be
 * empty while it is still being computed, and a button that would change
 * nothing says so instead of pretending.
 */
import { describe, expect, it } from 'vitest';

import {
  SLOW_MS,
  addAxis,
  clampShip,
  listState,
  moveAxis,
  removeAxis,
  shipState
} from '../src/lib/profile/tune';

const sum = (weights: Record<string, number>): number =>
  Object.values(weights).reduce((total, weight) => total + weight, 0);

describe('the shares add to one', () => {
  const start = { reasoning: 0.5, intelligence: 0.3, cost: 0.2 };

  it('moves one and the others follow, in proportion', () => {
    const after = moveAxis(start, 'reasoning', 0.8);
    expect(after.reasoning).toBeCloseTo(0.8, 12);
    expect(sum(after)).toBeCloseTo(1, 12);
    // intelligence had 3/5 of the remainder before, and still does
    expect(after.intelligence / after.cost).toBeCloseTo(0.3 / 0.2, 9);
  });

  it('holds at one at both ends of the slider', () => {
    for (const value of [0, 0.01, 0.5, 0.99, 1]) {
      expect(sum(moveAxis(start, 'cost', value))).toBeCloseTo(1, 12);
    }
    const all = moveAxis(start, 'cost', 1);
    expect(all.reasoning).toBeCloseTo(0, 12);
    expect(all.intelligence).toBeCloseTo(0, 12);
  });

  it('gives a new axis an even share and renormalises the rest', () => {
    const after = addAxis(start, 'latency');
    expect(after.latency).toBeCloseTo(0.25, 9);
    expect(sum(after)).toBeCloseTo(1, 12);
    expect(after.reasoning / after.intelligence).toBeCloseTo(0.5 / 0.3, 9);
  });

  it('adding an axis it already has changes nothing', () => {
    expect(addAxis(start, 'cost')).toEqual(start);
  });

  it('renormalises what is left when one is removed', () => {
    const after = removeAxis(start, 'cost');
    expect(after.cost).toBeUndefined();
    expect(sum(after)).toBeCloseTo(1, 12);
    expect(after.reasoning).toBeCloseTo(0.625, 9);
  });

  it('keeps the last axis: a profile is its weights', () => {
    expect(removeAxis({ cost: 1 }, 'cost')).toEqual({ cost: 1 });
  });

  it('ship stays between one and ten', () => {
    expect(clampShip(0)).toBe(1);
    expect(clampShip(40)).toBe(40);
    expect(clampShip(4.4)).toBe(4);
    expect(clampShip(Number.NaN)).toBe(1);
  });
});

describe('what the list panel may say', () => {
  const asking = { pending: true, waitedMs: 0, error: null, models: null };

  it('says it is ranking while it is ranking', () => {
    expect(listState(asking)).toBe('ranking');
  });

  it('says the box is busy only after eight seconds', () => {
    expect(listState({ ...asking, waitedMs: SLOW_MS - 1 })).toBe('ranking');
    expect(listState({ ...asking, waitedMs: SLOW_MS })).toBe('busy');
  });

  it('says it failed when it failed, even with an old list on screen', () => {
    expect(listState({ pending: false, waitedMs: 0, error: 'nope', models: [] })).toBe('error');
  });

  it('never calls a request in flight an empty list', () => {
    expect(listState({ ...asking, models: [] })).toBe('ranking');
    expect(listState({ pending: false, waitedMs: 0, error: null, models: null })).toBe('ranking');
  });

  it('says nothing ships only when an answer came back empty', () => {
    expect(listState({ pending: false, waitedMs: 0, error: null, models: [] })).toBe('empty');
    expect(listState({ pending: false, waitedMs: 0, error: null, models: ['a'] })).toBe('ready');
  });
});

describe('Ship now', () => {
  const ready = { shipping: false, ready: true };

  it('is pressable when the list would change', () => {
    const state = shipState({ ...ready, shipped: ['a', 'b'], next: ['b', 'a'] });
    expect(state.disabled).toBe(false);
    expect(state.title).toBe('');
  });

  it('says so when this is already what ships', () => {
    const state = shipState({ ...ready, shipped: ['a', 'b'], next: ['a', 'b'] });
    expect(state.disabled).toBe(true);
    expect(state.title).toBe('this is already what ships');
  });

  it('waits for the list rather than shipping a guess', () => {
    const state = shipState({ shipped: ['a'], next: [], shipping: false, ready: false });
    expect(state.disabled).toBe(true);
    expect(state.title).toBe('waiting for the list');
  });

  it('refuses to ship nothing', () => {
    const state = shipState({ ...ready, shipped: ['a'], next: [] });
    expect(state.disabled).toBe(true);
    expect(state.title).toBe('there is nothing to ship');
  });

  it('says what it is doing while it does it', () => {
    const state = shipState({ shipped: [], next: ['a'], shipping: true, ready: true });
    expect(state.disabled).toBe(true);
    expect(state.label).toBe('Shipping…');
  });

  it('is pressable for a profile that has never shipped', () => {
    expect(shipState({ ...ready, shipped: [], next: ['a'] }).disabled).toBe(false);
  });
});
