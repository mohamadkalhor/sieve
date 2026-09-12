/**
 * How fresh the numbers on screen are, in words a person reads at a glance.
 *
 * Kept apart from the rail so the arithmetic can be tested without a browser.
 */

import type { StatusRow } from '$lib/api/client';

export type Freshness = {
  /** "12 min ago", or null while the status has not arrived */
  ago: string | null;
  /** "hourly · next around 7:00 PM" */
  cadence: string;
  /**
   * The loop has missed at least one run it should have made.
   *
   * An hourly loop last seen three hours ago is not "updated 3 h ago", it is
   * broken, and the two must not look the same.
   */
  late: boolean;
};

const EVERY: Record<string, number> = {
  hourly: 60,
  daily: 24 * 60
};

/** `5 s ago` is noise; a minute is the smallest unit worth printing. */
export function ago(then: Date, now: Date): string {
  const minutes = Math.max(0, Math.round((now.getTime() - then.getTime()) / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${minutes % 60} min ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

export function freshness(status: StatusRow | null, now: Date = new Date()): Freshness {
  if (!status) return { ago: null, cadence: '', late: false };

  // the loop's own record is the better answer; a pull alone is a fallback
  const stamp = status.ran_at ?? status.pulled_at;
  const then = stamp ? new Date(stamp) : null;
  const every = EVERY[status.schedule];

  let cadence = status.schedule;
  let late = false;
  if (then && every) {
    // A timer that fires "hourly" fires on the hour, a few minutes late at
    // most, so the next run is the next hour boundary after the last one.
    const next = new Date(then);
    if (every === 60) {
      next.setMinutes(0, 0, 0);
      next.setHours(next.getHours() + 1);
    } else {
      next.setTime(then.getTime() + every * 60_000);
    }
    const time = next.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    cadence = `${status.schedule} · next around ${time}`;
    // one full period of grace past the expected run before calling it late
    late = now.getTime() - then.getTime() > (every + Math.min(every, 30)) * 60_000;
  }

  return { ago: then ? ago(then, now) : 'never', cadence, late };
}
