/**
 * The seats pane's decisions, as data (CONSOLE.md section 6.2).
 *
 * Kept out of the component so they can be tested without a browser, and
 * because every one of them is a rule about what may be *claimed*: a line under
 * a name that is not known is not drawn, and a group that is collapsed is a
 * view state rather than a setting.
 */
import type { SeatRow } from '$lib/api/client';

/**
 * Where the pane remembers which groups are collapsed.
 *
 * localStorage rather than the URL: collapsing is how somebody arranges their
 * own screen, not a place they can send to another person. Same shape as
 * `sieve:last-seat`.
 */
export const COLLAPSED_KEY = 'sieve:seats-collapsed';

/**
 * The second line of a seat's row: the first model the gateway holds.
 *
 * `live` is null when no chain was ever read and `[]` when the seat ships
 * nothing, and those are two different things: one says nothing at all, the
 * other says the truth out loud.
 */
export function secondLine(row: SeatRow): string | null {
  if (row.live === null) return null;
  if (row.live.length === 0) return 'nothing shipped yet';
  return row.live[0].name;
}

/** Whatever the stored value was, only strings are kept. */
export function parseCollapsed(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((one): one is string => typeof one === 'string')
      : [];
  } catch {
    // a value this pane did not write is not a reason to stop drawing the list
    return [];
  }
}

export function toggled(list: readonly string[], key: string): string[] {
  return list.includes(key) ? list.filter((one) => one !== key) : [...list, key];
}
