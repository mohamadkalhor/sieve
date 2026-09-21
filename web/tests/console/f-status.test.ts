/**
 * The status store: polling, the event stream, and what "unreachable" is
 * allowed to mean (CONSOLE.md sections 5.3 and 6.1).
 *
 * The clock is handed in, so a whole run cycle is driven by hand here and no
 * test waits a minute. What matters most is the difference between a server
 * that answered with a failure -- which is a reading, and the bar keeps the
 * last one -- and an API that did not answer at all.
 */
import { describe, expect, it, vi } from 'vitest';
import { fail, ok, type Result, type RunRow, type StatusRow } from '../../src/lib/api/client';
import { POLL_MS, StatusStore, type StatusDeps } from '../../src/lib/console/state/status.svelte';

type Handle = ReturnType<typeof globalThis.setTimeout>;

function run(over: Partial<RunRow> = {}): RunRow {
  return {
    id: 'run-1',
    step: 'full',
    requested_by: 'scheduler',
    started: '2026-09-19T04:00:00Z',
    finished: '2026-09-19T04:01:00Z',
    running: false,
    ok: true,
    summary: 'shipped 12 seats',
    error: null,
    seconds: 60,
    has_log: true,
    ...over
  };
}

function status(over: Partial<StatusRow> = {}): StatusRow {
  return {
    pulled_at: '2026-09-19T04:00:00Z',
    ran_at: '2026-09-19T04:01:00Z',
    schedule: 'hourly',
    sources_enabled: 3,
    telemetry_calls: 4015,
    telemetry_at: '2026-09-19T04:01:00Z',
    ...over
  };
}

const settle = () => new Promise((done) => setTimeout(done, 0));

/** A clock the test moves by hand, in place of the browser's. */
function clock() {
  let next = 0;
  const timers = new Map<number, { fn: () => void; ms: number }>();
  const setTimer: NonNullable<StatusDeps['setTimer']> = (fn, ms) => {
    const id = next++;
    timers.set(id, { fn, ms });
    return id as unknown as Handle;
  };
  const clearTimer: NonNullable<StatusDeps['clearTimer']> = (handle) => {
    timers.delete(handle as unknown as number);
  };
  return {
    setTimer,
    clearTimer,
    /** how long the one armed timer is set for, and how many are armed */
    armed: () => [...timers.values()].map((one) => one.ms),
    fire: () => {
      const [id] = [...timers.keys()];
      const one = timers.get(id as number);
      timers.delete(id as number);
      one?.fn();
    }
  };
}

function made(over: Partial<StatusDeps> = {}) {
  const c = clock();
  const events: (() => void)[] = [];
  const deps: StatusDeps = {
    api: { status: vi.fn(async () => ok(status())) },
    setTimer: c.setTimer,
    clearTimer: c.clearTimer,
    subscribe: (onEvent) => {
      events.push(onEvent);
      return () => {
        const at = events.indexOf(onEvent);
        if (at >= 0) events.splice(at, 1);
      };
    },
    ...over
  };
  return { ...c, store: new StatusStore(deps), events, api: deps.api as { status: ReturnType<typeof vi.fn> } };
}

describe('the status store', () => {
  it('asks at once, then once a minute, and stops asking when told to', async () => {
    const t = made();

    const stop = t.store.start();
    await settle();

    expect(t.api.status).toHaveBeenCalledTimes(1);
    expect(t.store.row?.schedule).toBe('hourly');
    expect(t.armed()).toEqual([POLL_MS]);

    t.fire();
    await settle();

    expect(t.api.status).toHaveBeenCalledTimes(2);
    // the next one is armed again, so one slow answer does not end the polling
    expect(t.armed()).toEqual([POLL_MS]);

    stop();

    expect(t.armed()).toEqual([]);
    expect(t.events).toHaveLength(0);
  });

  it('asks again the moment the event stream says something happened', async () => {
    const t = made();
    const stop = t.store.start();
    await settle();

    expect(t.api.status).toHaveBeenCalledTimes(1);
    expect(t.events).toHaveLength(1);

    t.events[0]();
    await settle();

    expect(t.api.status).toHaveBeenCalledTimes(2);
    stop();
  });

  it('keeps the last reading when the server answered with a failure', async () => {
    const api = {
      status: vi
        .fn()
        .mockResolvedValueOnce(ok(status({ reachable: 7 })))
        .mockResolvedValueOnce(fail({ code: 'server', message: 'the API said no', status: 500 }))
    };
    const t = made({ api });

    const stop = t.store.start();
    await settle();
    t.fire();
    await settle();

    expect(t.store.row?.reachable).toBe(7);
    // it said something: this is not an API that cannot be reached
    expect(t.store.unreachable).toBe(false);
    // but the failure is kept, because the bar has to be able to say it: `bar()`
    // draws a *reading*, and a failed read has no reading in it
    expect(t.store.failed?.status).toBe(500);
    expect(t.store.failed?.message).toBe('the API said no');
    // and a bad answer does not stop the clock
    expect(t.armed()).toEqual([POLL_MS]);
    stop();
  });

  it('forgets the failure as soon as a read answers again', async () => {
    const api = {
      status: vi
        .fn()
        .mockResolvedValueOnce(fail({ code: 'server', message: 'the API said no', status: 500 }))
        .mockResolvedValueOnce(ok(status({ reachable: 7 })))
    };
    const t = made({ api });

    const stop = t.store.start();
    await settle();
    expect(t.store.failed?.status).toBe(500);

    t.fire();
    await settle();

    expect(t.store.failed).toBeNull();
    stop();
  });

  it('says the API is unreachable only when nothing answered at all', async () => {
    const api = {
      status: vi.fn(async () => fail<StatusRow>({ code: 'offline', message: 'no', status: 0 }))
    };
    const t = made({ api });

    const stop = t.store.start();
    await settle();

    expect(t.store.unreachable).toBe(true);
    expect(t.store.row).toBeNull();
    expect(t.store.failed?.status).toBe(0);
    stop();
  });

  it('bumps the pulse once, when a run that was going stops going', async () => {
    const bump = vi.fn();
    const api = {
      status: vi
        .fn()
        .mockResolvedValueOnce(
          ok(status({ runs: { running: run({ running: true, finished: null }), last: null, last_by_step: {} } }))
        )
        .mockResolvedValueOnce(ok(status({ runs: { running: null, last: run(), last_by_step: {} } })))
        .mockResolvedValueOnce(ok(status({ runs: { running: null, last: run(), last_by_step: {} } })))
    };
    const t = made({ api, pulse: { bump } });

    t.store.start();
    await settle();
    expect(bump).not.toHaveBeenCalled();

    t.fire();
    await settle();
    expect(bump).toHaveBeenCalledTimes(1);

    t.fire();
    await settle();
    // the same news twice is not a second reason to refresh the screen
    expect(bump).toHaveBeenCalledTimes(1);
    t.store.stop();
  });

  it('never bumps for a server that does not report runs', async () => {
    const bump = vi.fn();
    const t = made({ pulse: { bump } });

    t.store.start();
    await settle();
    t.fire();
    await settle();

    expect(bump).not.toHaveBeenCalled();
    t.store.stop();
  });

  it('hands every reading to adopt, so an older server still signs a person in', async () => {
    const adopt = vi.fn();
    const t = made({ adopt });

    t.store.start();
    await settle();

    expect(adopt).toHaveBeenCalledTimes(1);
    expect(adopt.mock.calls[0][0]).toMatchObject({ telemetry_calls: 4015 });
    t.store.stop();
  });

  it('drops an answer that arrives after it was stopped', async () => {
    let answer: (value: Result<StatusRow>) => void = () => {};
    const api = {
      status: vi.fn(
        () => new Promise<Result<StatusRow>>((done) => { answer = done; })
      )
    };
    const t = made({ api });

    const stop = t.store.start();
    stop();
    answer(ok(status({ reachable: 9 })));
    await settle();

    expect(t.store.row).toBeNull();
  });
});
