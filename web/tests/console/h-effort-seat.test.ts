/**
 * Runs at, as a setting (EFFORT.md sections 1 and 6): it travels in the same
 * patch as the weights, it re-runs the preview, and Ship counts it -- even when
 * the new effort leaves every router id where it was, because the row's id
 * never changes with effort.
 */
import { describe, expect, it } from 'vitest';
import { SeatSession, type SeatApi } from '../../src/lib/console/state/seat.svelte';
import { ok, type Listed, type PreviewResult, type ProfileSettings } from '../../src/lib/api/client';
import type { Chain, Profile } from '../../src/lib/types';
import { diffLineup, shipLabel, shipWithEffort } from '../../src/lib/console/logic/diff';

const PROFILE = {
  name: 'build',
  modality: 'llm',
  purpose: 'the coder',
  weights: { quality: 1 },
  ship: 2
} as unknown as Profile;

const listed = (id: string): Listed => ({ id, name: id, local_ids: [id], score: 0.5 });

function previewOf(ids: string[]): PreviewResult {
  return {
    profile: 'build',
    ship: 2,
    models: ids.map(listed),
    next: [],
    settings: { weights: { quality: 1 }, ship: 2 },
    computed_at: 'now',
    warnings: []
  };
}

function harness(held: ProfileSettings, lineup: () => string[]) {
  const bodies: Record<string, unknown>[] = [];
  const saves: Record<string, unknown>[] = [];
  const timers: (() => void)[] = [];
  const api = {
    profile: async () => ok(PROFILE),
    profileSettings: async () => ok(held),
    chain: async () => ok({ profile: 'build', primary: 'a', fallbacks: ['b'] } as unknown as Chain),
    axes: async () => ok([]),
    costMultipliers: async () => ok({}),
    preview: async (_: string, body: Record<string, unknown>) => {
      bodies.push(body);
      return ok(previewOf(lineup()));
    },
    saveProfileSettings: async (_: string, body: Record<string, unknown>) => {
      saves.push(body);
      return ok(held);
    },
    applyProfile: async () => ok({ chain: { profile: 'build', primary: 'a', fallbacks: ['b'] }, combos: [] })
  } as unknown as SeatApi;
  const session = new SeatSession('build', {
    api,
    storage: null,
    setTimeout: (fn: () => void) => timers.push(fn),
    clearTimeout: () => {},
    now: () => 0,
    token: () => undefined
  });
  // A bounded number of rounds: the busy ticker re-arms itself while an
  // answer is in flight, so "until no timers are left" would never end.
  const flush = async () => {
    for (let round = 0; round < 4; round += 1) {
      for (const fn of timers.splice(0)) fn();
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    }
  };
  return { session, bodies, saves, flush };
}

const HELD = { weights: { quality: 1 }, ship: 2, effort: null } as ProfileSettings;

describe('Runs at', () => {
  it('opens at the stored effort, and sends it in every patch', async () => {
    const { session, bodies } = harness({ ...HELD, effort: 'high' }, () => ['a', 'b']);
    await session.open();
    expect(session.settings?.effort).toBe('high');
    expect(bodies[0].effort).toBe('high');
    expect(session.effortMoved).toBe(false);
  });

  it('leaves effort out of the patch on a server that never named one', async () => {
    const { session, bodies } = harness({ weights: { quality: 1 }, ship: 2 }, () => ['a', 'b']);
    await session.open();
    expect(session.settings?.effort).toBeUndefined();
    expect('effort' in bodies[0]).toBe(false);
  });

  it('writes and previews a new effort, and counts it as one change', async () => {
    const { session, bodies, saves, flush } = harness(HELD, () => ['a', 'b']);
    await session.open();
    expect(session.button.disabled).toBe(true);

    session.setEffort('medium');
    await flush();

    expect(saves.at(-1)?.effort).toBe('medium');
    expect(session.savedEffort).toBe('medium');
    expect(bodies.at(-1)?.effort).toBe('medium');
    // Same ids in the same order, and still a change: the chain carries it.
    expect(session.effortMoved).toBe(true);
    expect(session.button.disabled).toBe(false);
    expect(session.shipText).toBe('Ship 1 change');
    expect(session.effortMoves).toBe(0);
  });

  it('counts the rows the new effort moved, against the list it was picked over', async () => {
    let ids = ['a', 'b'];
    const { session, flush } = harness(HELD, () => ids);
    await session.open();
    ids = ['b', 'a'];
    session.setEffort('low');
    await flush();
    expect(session.effortMoves).toBe(2);
    // two moved rows and the effort itself
    expect(session.shipText).toBe('Ship 3 changes');
  });

  it('is no change once it is back where it shipped, and none after a ship', async () => {
    const { session, flush } = harness(HELD, () => ['a', 'b']);
    await session.open();
    session.setEffort('medium');
    await flush();
    session.setEffort(null);
    await flush();
    expect(session.effortMoved).toBe(false);

    session.setEffort('max');
    await flush();
    await session.ship();
    expect(session.shippedEffort).toBe('max');
    expect(session.effortMoved).toBe(false);
  });
});

describe('the ship button with an effort', () => {
  const diff = diffLineup(['a'], ['a']);
  const already = { disabled: true, title: 'this is already what ships', label: 'Ship now' };

  it('lifts only the "already what ships" refusal', () => {
    expect(shipWithEffort(already, true).disabled).toBe(false);
    expect(shipWithEffort(already, false)).toBe(already);
    const waiting = { disabled: true, title: 'waiting for the list', label: 'Ship now' };
    expect(shipWithEffort(waiting, true)).toBe(waiting);
  });

  it('adds the effort to the count', () => {
    expect(shipLabel(diff, { disabled: false, label: 'Ship now' }, 1)).toBe('Ship 1 change');
    expect(shipLabel(diffLineup(['a', 'b'], ['b', 'a']), { disabled: false, label: 'Ship now' }, 1)).toBe(
      'Ship 3 changes'
    );
    expect(shipLabel(diff, { disabled: false, label: 'Ship now' })).toBe('Ship now');
  });
});
