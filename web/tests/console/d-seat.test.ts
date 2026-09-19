/**
 * One seat's session: what it reads, what it writes, and the two orders it
 * keeps (CONSOLE.md sections 5.2 and 5.3).
 *
 * The interesting failures here are all about time -- an answer that arrives
 * after the person has moved on, a write that overtakes a newer one, a ship that
 * applies a lineup nobody can see -- so the clock, the client and the storage
 * are all handed in, and every test below moves them by hand.
 */
import { describe, expect, it } from 'vitest';
import { SeatSession, type SeatApi, type SeatSessionDeps } from '../../src/lib/console/state/seat.svelte';
import { ok, type ApiError, type Listed, type PreviewResult, type ProfileSettings, type Result, type SeatRow } from '../../src/lib/api/client';
import { DEBOUNCE_MS, SLOW_MS } from '../../src/lib/profile/tune';
import type { Chain, Profile } from '../../src/lib/types';

/* -------------------------------------------------------------------------- */
/* fixtures                                                                    */
/* -------------------------------------------------------------------------- */

const PROFILE = {
  name: 'fast',
  modality: 'llm',
  purpose: 'cheap and quick',
  weights: { quality: 0.7, cost: 0.3 },
  ship: 4,
  mode: 'auto',
  manual: [],
  pinned: [],
  removed: [],
  needs: [],
  prefix_weights: {}
} as unknown as Profile;

/** What the settings route holds: not the profile's own numbers. */
const HELD = {
  weights: { quality: 0.6, cost: 0.4 },
  ship: 3,
  mode: 'auto',
  manual: [],
  pinned: [],
  removed: [],
  needs: [],
  prefix_weights: {}
} as unknown as ProfileSettings;

const CHAIN = { profile: 'fast', primary: 'a/b', fallbacks: ['c/d'] } as unknown as Chain;

const AXES = [
  { name: 'quality', label: 'Quality', meaning: 'how good', modality: 'llm' },
  { name: 'cost', label: 'Cost', meaning: 'how cheap', modality: 'llm' },
  { name: 'speed', label: 'Speed', meaning: 'how fast', modality: 'llm' }
] as unknown as import('../../src/lib/api/client').AxisRow[];

function listed(id: string, score = 0.5): Listed {
  return { id, name: id, local_ids: [id], score, scored: true } as Listed;
}

function previewOf(ids: string[], extra: Partial<PreviewResult> = {}): PreviewResult {
  const models = ids.map((id, index) => listed(id, 1 - index / 10));
  return {
    profile: 'fast',
    mode: 'auto',
    ship: 3,
    models,
    next: [],
    pool: models,
    settings: HELD,
    computed_at: '2026-09-19T00:00:00Z',
    warnings: [],
    ...extra
  };
}

function bad(code = 'unreachable'): { ok: false; error: ApiError } {
  return { ok: false, error: { code, message: `${code} says no`, status: 400 } };
}

/* -------------------------------------------------------------------------- */
/* harness                                                                     */
/* -------------------------------------------------------------------------- */

interface Call {
  name: string;
  args: unknown[];
}

function fakeApi() {
  const calls: Call[] = [];
  const handlers: Record<string, (...args: never[]) => Promise<Result<unknown>>> = {
    profile: async () => ok(PROFILE),
    profileSettings: async () => ok(HELD),
    chain: async () => ok(CHAIN),
    axes: async () => ok(AXES),
    costMultipliers: async () => ok({ openai: 1.5 }),
    preview: async () => ok(previewOf(['a/b', 'c/d'])),
    saveProfileSettings: async () => ok(HELD),
    applyProfile: async () => ok({ profile: 'fast', chain: CHAIN, combos: ['a/b'], shipped_at: 'now' }),
    linkUnscored: async () => ok({ model_id: 'a/b', local_id: 'x/y' }),
    history: async () => ok([{ who: 'you', when: '2026-09-19T00:00:00Z', what: 'weights' }]),
    newProfile: async () => ok(PROFILE),
    renameProfile: async () => ok(PROFILE),
    removeProfile: async () => ok({}),
    setPurpose: async () => ok({ ...PROFILE, purpose: 'new' })
  };
  const api: Record<string, (...args: never[]) => Promise<Result<unknown>>> = {};
  for (const name of Object.keys(handlers)) {
    api[name] = (...args: never[]) => {
      calls.push({ name, args });
      return handlers[name](...args);
    };
  }
  return {
    api: api as unknown as SeatApi,
    calls,
    to: (name: string) => calls.filter((call) => call.name === name),
    order: () => calls.map((call) => call.name),
    answer(name: string, fn: (...args: never[]) => Promise<Result<unknown>>): void {
      handlers[name] = fn;
    }
  };
}

function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

function fakeClock() {
  let at = 1_000;
  let next = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    deps: {
      setTimeout: (fn: () => void, ms: number) => {
        const id = next++;
        timers.set(id, { at: at + ms, fn });
        return id;
      },
      clearTimeout: (id: number) => {
        timers.delete(id);
      },
      now: () => at
    },
    at: () => at,
    async advance(ms: number) {
      at += ms;
      for (const [id, timer] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
        if (timer.at > at) continue;
        timers.delete(id);
        timer.fn();
        await settle();
      }
    }
  };
}

/** Let every promise that is already resolved run to completion. */
async function settle(times = 8): Promise<void> {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

function harness(over: Partial<SeatSessionDeps> = {}) {
  const fake = fakeApi();
  const clock = fakeClock();
  const store = new Map<string, string>();
  const patches: { name: string; part: Partial<SeatRow> }[] = [];
  const saved: string[] = [];
  const shipped: string[] = [];
  const deps: SeatSessionDeps = {
    api: fake.api,
    storage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
      key: () => null,
      length: 0
    } as Storage,
    setTimeout: clock.deps.setTimeout,
    clearTimeout: clock.deps.clearTimeout,
    now: clock.deps.now,
    token: () => 'tok',
    patch: (name, part) => patches.push({ name, part }),
    onSaved: (name) => saved.push(name),
    onShipped: (name) => shipped.push(name),
    ...over
  };
  const session = new SeatSession('fast', deps);
  return { session, fake, clock, store, patches, saved, shipped, deps };
}

/* -------------------------------------------------------------------------- */
/* tests                                                                       */
/* -------------------------------------------------------------------------- */

describe('opening a seat', () => {
  it('reads the seat, then asks what it would ship', async () => {
    const { session, fake } = harness();
    await session.open();
    expect(session.profile?.name).toBe('fast');
    // The stored settings win over the profile's own weights, and the first
    // preview carries exactly them.
    expect(session.settings?.weights).toEqual(HELD.weights);
    expect(session.settings?.ship).toBe(3);
    const [, body] = fake.to('preview')[0].args;
    expect((body as { weights: Record<string, number> }).weights).toEqual(HELD.weights);
    expect(session.listing).toBe('ready');
  });

  it('keeps the profile axes it does not score by yet, for "Add an axis"', async () => {
    const { session } = harness();
    await session.open();
    expect(session.spare.map((axis) => axis.name)).toEqual(['speed']);
    expect(session.labels.quality).toBe('Quality');
  });

  it('rides the browser token on every request', async () => {
    const { session, fake } = harness();
    await session.open();
    for (const call of fake.calls) {
      const last = call.args[call.args.length - 1] as { token?: string } | undefined;
      expect(last?.token).toBe('tok');
    }
  });

  it('says the seat is gone when the profile is not there', async () => {
    const { session, fake } = harness();
    fake.answer('profile', async () => bad('not_found'));
    await session.open();
    expect(session.gone?.code).toBe('not_found');
    expect(session.loading).toBe(false);
    expect(session.profile).toBe(null);
  });

  it('still opens when the settings route is not there: the profile answers', async () => {
    const { session, fake } = harness();
    fake.answer('profileSettings', async () => bad('not_found'));
    await session.open();
    expect(session.settings?.weights).toEqual(PROFILE.weights);
  });

  it('drops an answer for a seat that was left', async () => {
    const { session, fake } = harness();
    const profile = deferred<Result<Profile>>();
    fake.answer('profile', () => profile.promise);
    const opening = session.open();
    await settle();
    session.close();
    profile.settle(ok(PROFILE));
    await opening;
    await settle();
    expect(session.profile).toBe(null);
    expect(fake.to('preview')).toHaveLength(0);
  });
});

describe('the list', () => {
  it('ranks, then answers: nothing ships is never said early', async () => {
    const { session, fake } = harness();
    const answer = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => answer.promise);
    const opening = session.open();
    await settle();

    expect(session.listing).toBe('ranking');
    expect(session.button.disabled).toBe(true);
    expect(session.button.title).toBe('waiting for the list');

    answer.settle(ok(previewOf([])));
    await opening;
    expect(session.listing).toBe('empty');
    expect(session.button.title).toBe('there is nothing to ship');
  });

  it('says it is busy only after the wait is long', async () => {
    const { session, fake, clock } = harness();
    const answer = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => answer.promise);
    const opening = session.open();
    await settle();
    expect(session.listing).toBe('ranking');

    await clock.advance(SLOW_MS);
    expect(session.waitedMs).toBeGreaterThanOrEqual(SLOW_MS);
    expect(session.listing).toBe('busy');

    answer.settle(ok(previewOf(['a/b'])));
    await opening;
    expect(session.listing).toBe('ready');
  });

  it('keeps the last lineup when an answer fails, and says why', async () => {
    const { session, fake } = harness();
    await session.open();
    expect(session.preview?.models).toHaveLength(2);

    fake.answer('preview', async () => bad('timeout'));
    await session.refresh();
    expect(session.listing).toBe('error');
    expect(session.failed).toBe('timeout says no');
    expect(session.preview?.models).toHaveLength(2);
  });

  it('lets only the newest question be answered', async () => {
    const { session, fake } = harness();
    await session.open();
    const first = deferred<Result<PreviewResult>>();
    const second = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => first.promise);
    void session.refresh();
    fake.answer('preview', () => second.promise);
    void session.refresh();
    await settle();

    second.settle(ok(previewOf(['new/one'])));
    await settle();
    first.settle(ok(previewOf(['old/one'])));
    await settle();
    expect(session.preview?.models.map((row) => row.id)).toEqual(['new/one']);
  });
});

describe('editing', () => {
  it('writes once for a burst of edits, and previews the same weights', async () => {
    const { session, fake, clock } = harness();
    await session.open();

    session.edit({ ...session.settings!, weights: { quality: 0.5, cost: 0.5 } });
    session.edit({ ...session.settings!, ship: 5 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    const writes = fake.to('saveProfileSettings');
    expect(writes).toHaveLength(1);
    expect(fake.to('preview')).toHaveLength(2); // the one from open, and this
    const body = writes[0].args[1] as { weights: Record<string, number>; ship: number };
    expect(body.weights).toEqual({ quality: 0.5, cost: 0.5 });
    expect(body.ship).toBe(5);
    expect(fake.to('preview')[1].args[1]).toEqual(body);
  });

  it('refuses the last axis in words, and writes nothing', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    session.edit({ error: 'A profile is its weights: keep at least one axis.' });
    await clock.advance(DEBOUNCE_MS);
    expect(session.said?.text).toContain('keep at least one axis');
    expect(fake.to('saveProfileSettings')).toHaveLength(0);
  });

  it('says when a write failed, and keeps the weights on screen', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    fake.answer('saveProfileSettings', async () => bad('invalid'));
    session.edit({ ...session.settings!, ship: 9 });
    await clock.advance(DEBOUNCE_MS);
    await settle();
    expect(session.said).toEqual({ ok: false, text: 'invalid says no' });
    expect(session.settings?.ship).toBe(9);
    expect(session.saving).toBe(false);
  });

  it('remembered locks are this browser only: a lock writes nothing', async () => {
    const { session, fake, clock, store } = harness();
    await session.open();
    const before = fake.to('saveProfileSettings').length;

    session.edit({ ...session.settings!, locked: ['quality'] });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    expect(JSON.parse(store.get('sieve:locks:fast') ?? '[]')).toEqual(['quality']);
    expect(fake.to('saveProfileSettings')).toHaveLength(before);
  });

  it('a lock on an axis this profile no longer has is dropped on open', async () => {
    const { session } = harness();
    await session.open();
    expect(session.settings?.locked).toEqual([]);
  });

  it('remembered locks come back with the seat', async () => {
    const { deps, store } = harness();
    store.set('sieve:locks:fast', JSON.stringify(['cost', 'gone']));
    const reopened = new SeatSession('fast', deps);
    await reopened.open();
    expect(reopened.settings?.locked).toEqual(['cost']);
  });

  it('restores the settings the seat opened with', async () => {
    const { session } = harness();
    await session.open();
    session.edit({ ...session.settings!, weights: { quality: 0.9, cost: 0.1 } });
    session.restoreOpened();
    expect(session.settings?.weights).toEqual(HELD.weights);
  });
});

describe('shipping', () => {
  it('saves, checks, then applies -- in that order', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    // What these weights would ship is not what the connector holds, so there
    // is something to ship.
    fake.answer('preview', async () => ok(previewOf(['e/f', 'a/b'])));
    session.edit({ ...session.settings!, weights: { quality: 0.2, cost: 0.8 } });
    await clock.advance(DEBOUNCE_MS);
    await settle();
    expect(session.button.disabled).toBe(false);

    await session.ship();

    // The write comes before the apply, and the apply only follows a check
    // asked for the exact settings that were written.
    const order = fake.order();
    const body = fake.to('saveProfileSettings').at(-1)!.args[1] as {
      weights: Record<string, number>;
      ship: number;
    };
    expect(order.indexOf('saveProfileSettings')).toBeLessThan(order.indexOf('applyProfile'));
    const checks = fake.to('preview').map((call) => JSON.stringify(call.args[1]));
    expect(checks).toContain(JSON.stringify(body));
    expect(body.weights).toEqual({ quality: 0.2, cost: 0.8 });
    expect(session.said).toEqual({ ok: true, text: 'Shipped as a/b.' });
    expect(session.shipping).toBe(false);
  });

  it('never says "already what ships" about a draft nobody has checked', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    expect(session.button.title).toBe('this is already what ships');

    // The slider has moved: the last answer describes weights nobody is
    // looking at any more, so the button may not claim the draft is live.
    fake.answer('preview', async () => ok(previewOf(['e/f'])));
    session.edit({ ...session.settings!, ship: 6 });
    expect(session.button.disabled).toBe(true);
    expect(session.button.title).toBe('waiting for the list');

    await clock.advance(DEBOUNCE_MS);
    await settle();
    expect(session.button.disabled).toBe(false);
    expect(session.button.title).toBe('');
  });

  it('ships without a second write when this draft is already saved', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    fake.answer('preview', async () => ok(previewOf(['e/f'])));
    session.edit({ ...session.settings!, ship: 6 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    const writes = fake.to('saveProfileSettings').length;
    await session.ship();
    expect(fake.to('saveProfileSettings')).toHaveLength(writes);
    expect(fake.to('applyProfile')).toHaveLength(1);
  });

  it('writes and checks before applying when the check is not there yet', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    const check = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => check.promise);
    const write = deferred<Result<ProfileSettings>>();
    fake.answer('saveProfileSettings', () => write.promise);

    session.edit({ ...session.settings!, ship: 7 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    // Nothing has answered for these weights, so there is nothing to press.
    expect(session.button.disabled).toBe(true);
    await session.ship();
    expect(fake.to('applyProfile')).toHaveLength(0);

    write.settle(ok(HELD));
    check.settle(ok(previewOf(['e/f'])));
    await settle();
    expect(session.button.disabled).toBe(false);
    await session.ship();
    expect(fake.to('applyProfile')).toHaveLength(1);
  });

  it('does not apply when the list could not be checked', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    fake.answer('preview', async () => bad('timeout'));
    session.edit({ ...session.settings!, ship: 7 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    expect(session.button.disabled).toBe(true);
    await session.ship();
    expect(fake.to('applyProfile')).toHaveLength(0);
    expect(session.said).toBe(null);
  });

  it('does not apply when the write failed', async () => {
    const { session, fake, clock } = harness();
    await session.open();
    fake.answer('saveProfileSettings', async () => bad('invalid'));
    fake.answer('preview', async () => ok(previewOf(['e/f'])));
    session.edit({ ...session.settings!, ship: 7 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    expect(session.said?.ok).toBe(false);
    expect(session.button.disabled).toBe(false);
    await session.ship();
    expect(fake.to('applyProfile')).toHaveLength(0);
    expect(session.said).toEqual({ ok: false, text: 'invalid says no' });
  });

  it('will not ship while there is nothing to ship', async () => {
    const { session, fake } = harness();
    await session.open();
    // The open preview and the live chain agree: a/b, c/d.
    fake.answer('preview', async () => ok(previewOf(['a/b', 'c/d'])));
    await session.refresh();
    expect(session.button.disabled).toBe(true);
    expect(session.button.title).toBe('this is already what ships');
    await session.ship();
    expect(fake.to('applyProfile')).toHaveLength(0);
  });

  it('tells the chain to whoever watches when it ships', async () => {
    const { session, fake, clock, shipped } = harness();
    await session.open();
    fake.answer('preview', async () => ok(previewOf(['e/f'])));
    session.edit({ ...session.settings!, ship: 6 });
    await clock.advance(DEBOUNCE_MS);
    await settle();
    await session.ship();
    expect(shipped).toEqual(['fast']);
    expect(session.chain?.primary).toBe('a/b');
  });
});

describe('the seats list follows the seat', () => {
  it('patches only from a lineup that is saved and current', async () => {
    const { session, fake, clock, patches } = harness();
    await session.open();
    patches.length = 0;

    const write = deferred<Result<ProfileSettings>>();
    fake.answer('saveProfileSettings', () => write.promise);
    session.edit({ ...session.settings!, weights: { quality: 0.1, cost: 0.9 } });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    // The preview answered, but the write has not: the list keeps the old truth.
    expect(patches).toHaveLength(0);

    write.settle(ok(HELD));
    await settle();
    expect(patches).toHaveLength(1);
    expect(patches[0].name).toBe('fast');
    expect(patches[0].part.lineup?.map((row) => row.id)).toEqual(['a/b', 'c/d']);
    expect(patches[0].part.in_step).toBe(true);
  });

  it('never patches from a question that is no longer current', async () => {
    const { session, fake, clock, patches } = harness();
    await session.open();
    patches.length = 0;

    const slow = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => slow.promise);
    session.edit({ ...session.settings!, ship: 4 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    // A newer edit supersedes the question in flight; its answer must not
    // reach the list even though the write it belonged to succeeded.
    const fresh = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => fresh.promise);
    session.edit({ ...session.settings!, ship: 5 });
    await clock.advance(DEBOUNCE_MS);
    await settle();

    slow.settle(ok(previewOf(['stale/one'])));
    await settle();
    expect(patches).toHaveLength(0);

    fresh.settle(ok(previewOf(['fresh/one'])));
    await settle();
    expect(patches).toHaveLength(1);
    expect(patches[0].part.lineup?.map((row) => row.id)).toEqual(['fresh/one']);
  });

  it('patches nothing when the seat is left with an answer in flight', async () => {
    const { session, fake, clock, patches } = harness();
    await session.open();
    patches.length = 0;

    const slow = deferred<Result<PreviewResult>>();
    fake.answer('preview', () => slow.promise);
    session.edit({ ...session.settings!, ship: 4 });
    await clock.advance(DEBOUNCE_MS);
    await settle();
    session.close();

    slow.settle(ok(previewOf(['gone/one'])));
    await settle();
    expect(patches).toHaveLength(0);
  });
});

describe('the seat menu', () => {
  it('links an unlinked router id, then pins it', async () => {
    const { session, fake } = harness();
    fake.answer('preview', async () =>
      ok(previewOf(['a/b'], { unlinked: [{ local_id: 'x/y', name: 'x/y' }] }))
    );
    await session.open();
    expect(session.preview?.unlinked).toHaveLength(1);

    await session.linkThen('x/y', (id) => ({ ...session.settings!, pinned: [id] }));
    expect(session.preview?.unlinked).toHaveLength(0);
    expect(session.settings?.pinned).toEqual(['a/b']);
  });

  it('says why a link failed', async () => {
    const { session, fake } = harness();
    await session.open();
    fake.answer('linkUnscored', async () => bad('not_found'));
    await session.linkThen('x/y', () => session.settings!);
    expect(session.said?.text).toBe('not_found says no');
    expect(session.linking).toBe(null);
  });

  it('copies, renames and deletes, and answers with where to go', async () => {
    const { session, fake } = harness();
    await session.open();
    expect(await session.copy('fast_two')).toBe('fast_two');
    const [body] = fake.to('newProfile')[0].args;
    expect(body).toMatchObject({ name: 'fast_two', from: 'fast', copy_from: 'fast', modality: 'llm' });

    expect(await session.rename('faster')).toBe('faster');
    expect(await session.destroy()).toBe(true);
  });

  it('retries a delete with force when the seat is in use', async () => {
    const { session, fake } = harness();
    await session.open();
    let first = true;
    fake.answer('removeProfile', async () => {
      if (first) {
        first = false;
        return bad('in_use');
      }
      return ok({});
    });
    expect(await session.destroy()).toBe(true);
    expect(fake.to('removeProfile').map((call) => call.args[1])).toEqual([false, true]);
  });

  it('keeps the history it had when a read fails', async () => {
    const { session, fake } = harness();
    await session.open();
    await session.history();
    expect(session.historyRows).toHaveLength(1);

    fake.answer('history', async () => bad('timeout'));
    await session.history();
    expect(session.historyRows).toHaveLength(1);
    expect(session.historyError?.code).toBe('timeout');
  });

  it('opens the drawer and asks for the history once', async () => {
    const { session, fake } = harness();
    await session.open();
    await session.openHistory();
    expect(session.historyOpen).toBe(true);
    expect(fake.to('history')).toHaveLength(1);
    await session.openHistory();
    expect(session.historyOpen).toBe(false);
  });

  it('saves the purpose on its own and tells the list', async () => {
    const { session, fake, patches, saved } = harness();
    await session.open();
    await session.setPurpose('  cheaper  ');
    expect(fake.to('setPurpose')[0].args[1]).toBe('cheaper');
    expect(session.profile?.purpose).toBe('new');
    expect(patches.at(-1)?.part.purpose).toBe('cheaper');
    expect(saved).toEqual(['fast']);
  });
});
