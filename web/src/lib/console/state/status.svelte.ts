/**
 * What the server is doing, as the status bar reads it (CONSOLE.md sections 5.3
 * and 6.1).
 *
 * Polls once a minute, and asks again the moment `/v1/events` says something
 * happened, so a run that finishes while a person is watching shows up without
 * waiting for the timer. `runs.running` going from a row to null means a run
 * just ended and every list on screen is now yesterday's: that is the moment
 * `pulse` is bumped (the layout hands in the app's `runPulse`).
 *
 * It is also the one place that learns who is signed in on a server whose
 * `/v1/me` has not landed, so it hands every answer to `adopt`.
 *
 * Nothing here is a component: `start()` works outside one and returns the
 * function that stops it, and the timer, the event stream and the clock are all
 * injected, so a test can drive a whole run cycle without waiting a minute.
 */
import { api as realApi, subscribe as realSubscribe } from '$lib/api/client';
import type { Result, StatusRow } from '$lib/api/client';
import type { StatusStoreLike } from '../contracts';

/** How often the bar asks when nothing is happening. */
export const POLL_MS = 60_000;

/** The one route this store reads. */
export interface StatusApi {
  status(): Promise<Result<StatusRow>>;
}

type Timer = ReturnType<typeof globalThis.setTimeout>;

export interface StatusDeps {
  api?: StatusApi;
  /** `/v1/events`: called with the fact that something happened */
  subscribe?: (onEvent: () => void) => () => void;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (handle: Timer) => void;
  /** every answer, so an older server's `/v1/status` still signs a person in */
  adopt?: (row: StatusRow | null) => void;
  /** bumped when a run that was going stops going */
  pulse?: { bump(): void };
}

export class StatusStore implements StatusStoreLike {
  /** the last answer, or null before the first one */
  row = $state<StatusRow | null>(null);
  /**
   * The API did not answer at all.
   *
   * A server that answered with a failure is not this: it said something, and
   * the bar goes on showing the last reading it was given rather than claiming
   * the API is down when it plainly is not.
   */
  unreachable = $state(false);

  #deps: Required<StatusDeps>;
  #timer: Timer | null = null;
  #unsubscribe: (() => void) | null = null;
  #stopped = false;
  /** whether the last answer we saw had a run in it */
  #wasRunning = false;

  constructor(deps: StatusDeps = {}) {
    this.#deps = {
      api: realApi,
      subscribe: (onEvent) => realSubscribe(() => onEvent()),
      setTimer: (fn, ms) => globalThis.setTimeout(fn, ms),
      clearTimer: (handle) => globalThis.clearTimeout(handle),
      adopt: () => {},
      pulse: { bump: () => {} },
      ...deps
    };
  }

  /** Begins polling, and returns the function that stops it. */
  start(): () => void {
    if (this.#timer !== null) return () => this.stop();
    this.#stopped = false;
    void this.refresh();
    this.#schedule();
    this.#unsubscribe = this.#deps.subscribe(() => void this.refresh());
    return () => this.stop();
  }

  stop(): void {
    this.#stopped = true;
    if (this.#timer !== null) this.#deps.clearTimer(this.#timer);
    this.#timer = null;
    this.#unsubscribe?.();
    this.#unsubscribe = null;
  }

  async refresh(): Promise<void> {
    const result = await this.#deps.api.status();
    // a late answer after teardown belongs to a store nobody is reading
    if (this.#stopped) return;
    if (!result.ok) {
      if (result.error.status === 0) this.unreachable = true;
      return;
    }
    this.row = result.value;
    this.unreachable = false;
    this.#deps.adopt(result.value);
    this.#watch(result.value);
  }

  /** A run that was going and is not any more: everything on screen is stale. */
  #watch(row: StatusRow): void {
    const running = row.runs?.running ?? null;
    if (running) {
      this.#wasRunning = true;
      return;
    }
    if (!this.#wasRunning) return;
    this.#wasRunning = false;
    this.#deps.pulse.bump();
  }

  #schedule(): void {
    this.#timer = this.#deps.setTimer(() => {
      this.#timer = null;
      if (this.#stopped) return;
      void this.refresh();
      this.#schedule();
    }, POLL_MS);
  }
}
