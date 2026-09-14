/**
 * Why the list is short.
 *
 * Raising the list length and watching nothing happen is the complaint this
 * file answers. The cap is the last thing that runs: before it, `controlled_ids`
 * drops everything unreachable, everything the profile's constraints refused,
 * everything a cheaper model already beats on every axis it is weighed on, and
 * everything below the confidence floor. A seat with six survivors will show
 * six whether the cap says six or sixty, and the page has to say so in words
 * rather than leave him turning a knob that is not attached to anything.
 *
 * The counting is done here, away from the page, so the same arithmetic can be
 * asserted in `tests/shortlist.test.ts`.
 */
import type { Rank } from '../types';

export type StatusOf = (modelId: string) => 'active' | 'pinned' | 'removed';

export interface ShortList {
  /** how many rows the list actually draws */
  shown: number;
  /** the cap he set */
  cap: number;
  /** never reached by any connector */
  unreachable: number;
  /** under the confidence floor */
  belowFloor: number;
  /** beaten outright by another model */
  dominated: number;
  /** refused by a constraint, by reason: `{ tools: 2, min_context: 1 }` */
  excludedBy: Record<string, number>;
  /** total of `excludedBy` */
  excluded: number;
  /** taken off this seat by hand */
  held: number;
  /** the refused rows themselves, for the "show" panel */
  rows: { model_id: string; reason: string }[];
}

/** The floor is a confidence floor, so `min_confidence` is its own bucket. */
const FLOOR = 'min_confidence';

export function shortList(
  ranks: Rank[],
  options: { shown: number; cap: number; statusOf?: StatusOf }
): ShortList {
  const statusOf = options.statusOf ?? (() => 'active' as const);
  const out: ShortList = {
    shown: options.shown,
    cap: options.cap,
    unreachable: 0,
    belowFloor: 0,
    dominated: 0,
    excludedBy: {},
    excluded: 0,
    held: 0,
    rows: []
  };

  for (const rank of ranks) {
    // One row, one reason: the first thing that stopped it is the thing to
    // tell him about, in the order the server applies them.
    if (statusOf(rank.model_id) === 'removed') {
      out.held += 1;
      out.rows.push({ model_id: rank.model_id, reason: 'held off this seat' });
      continue;
    }
    if (!rank.reachable) {
      out.unreachable += 1;
      out.rows.push({ model_id: rank.model_id, reason: 'not reachable' });
      continue;
    }
    if (rank.excluded_by === FLOOR) {
      out.belowFloor += 1;
      out.rows.push({ model_id: rank.model_id, reason: 'below the confidence floor' });
      continue;
    }
    if (rank.excluded_by) {
      out.excludedBy[rank.excluded_by] = (out.excludedBy[rank.excluded_by] ?? 0) + 1;
      out.excluded += 1;
      out.rows.push({ model_id: rank.model_id, reason: `excluded by ${rank.excluded_by}` });
      continue;
    }
    if (rank.dominated_by) {
      out.dominated += 1;
      out.rows.push({
        model_id: rank.model_id,
        reason: `beaten on every axis by ${rank.dominated_by}`
      });
    }
  }
  return out;
}

/** The one line under the list. Only the parts that are not zero. */
export function shortListLine(counts: ShortList, floor: number): string {
  const parts = [`${counts.shown} shown of list length ${counts.cap}`];
  if (counts.unreachable) parts.push(`${counts.unreachable} unreachable`);
  if (counts.belowFloor) parts.push(`${counts.belowFloor} below floor ${floor.toFixed(2)}`);
  if (counts.dominated) parts.push(`${counts.dominated} dominated`);
  if (counts.excluded) {
    const why = Object.entries(counts.excludedBy)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([reason, n]) => `${reason} ${n}`)
      .join(', ');
    parts.push(`${counts.excluded} excluded (${why})`);
  }
  if (counts.held) parts.push(`${counts.held} held or disabled`);
  return parts.join(' · ');
}
