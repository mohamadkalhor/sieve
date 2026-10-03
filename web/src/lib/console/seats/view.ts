/**
 * The seats pane's decisions, as data (CONSOLE.md section 6.2).
 *
 * Kept out of the component so they can be tested without a browser, and
 * because every one of them is a rule about what may be *claimed*: a line under
 * a name that is not known is not drawn, and a group that is collapsed is a
 * view state rather than a setting.
 */
import type { SeatRow } from '$lib/api/client';
import { effortWord } from '../seat/rows';

/**
 * Where the pane remembers which groups are collapsed.
 *
 * localStorage rather than the URL: collapsing is how somebody arranges their
 * own screen, not a place they can send to another person. Same shape as
 * `sieve:last-seat`.
 */
export const COLLAPSED_KEY = 'sieve:seats-collapsed';

/**
 * The second line of a seat's row: the first model the gateway holds, and
 * the effort the seat runs at -- "GPT-6 Astra · medium" (EFFORT.md section 6).
 *
 * `live` is null when no chain was ever read and `[]` when the seat ships
 * nothing, and those are two different things: one says nothing at all, the
 * other says the truth out loud. A seat with no effort set says no effort:
 * "any" is the absence of one, and the `effort?` note says that better.
 */
export function secondLine(row: SeatRow): string | null {
  if (row.live === null) return null;
  if (row.live.length === 0) return 'nothing shipped yet';
  return row.effort ? `${row.live[0].name} · ${effortWord(row.effort)}` : row.live[0].name;
}

/**
 * The amber `effort?` beside a seat that still scores its models at their top
 * effort while shipping one that publishes several. A seat whose models have
 * one setting each has nothing to set, so it is not nagged.
 */
export function effortNote(row: SeatRow): { text: string; title: string } | null {
  if (row.modality !== 'llm' || row.effort != null || row.multi_mode !== true) return null;
  return {
    text: 'effort?',
    title: 'Runs at is not set: models are scored at their top effort'
  };
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
