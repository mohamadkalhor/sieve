/**
 * What the table shows, as data (CONSOLE.md section 6.6, REVIEW.md finding 10).
 *
 * The table has five lists and a header, and three of them cannot be a `Listed`
 * row: a `missing` id is `{id, name}` and a `leaving` id is a string out of the
 * chain. Filling those in with `score: 0` would turn "nobody said" into "it
 * scored nothing", so the row is a discriminated union instead -- a `ranked`
 * row carries the server's `Listed`, a `bare` row carries an id and a name and
 * nothing else -- and the components draw a dash with a reason rather than a
 * number for the second kind.
 *
 * All of it is pure: no Svelte, no fetch, no clock. The tests in
 * `web/tests/console/d-rows.test.ts` are where the ordering, the deduplication
 * and the wording are pinned down.
 */
import type { Listed, Need, PreviewResult, ProfileMode } from '$lib/api/client';
import { NEEDS } from '$lib/api/client';
import { NEED_TAG } from '../logic/abilities';
import type { LineupDiff, RowMove } from '../logic/diff';
import type { IconName } from '../ui/icon-names';

/** A row the server ranked: it has a score, a cost and capabilities. */
export interface RankedRow {
  kind: 'ranked';
  id: string;
  name: string;
  /** 1-based inside the ranking; blank for a row outside it */
  rank: string;
  /** a short word after the name that is not a diff move: `leaves` */
  note: string;
  row: Listed;
}

/** A row we know by id and nothing else: a missing or leaving model. */
export interface BareRow {
  kind: 'bare';
  id: string;
  name: string;
  rank: string;
  note: string;
  /** why it is here without a ranking, so the row can say which absence it is */
  why: 'leaving' | 'missing';
}

export type DisplayRow = RankedRow | BareRow;

/** The row's action buttons, in the order section 6.6 draws them. */
export type RowAction = 'pin' | 'remove' | 'restore' | 'add' | 'up' | 'down' | 'drop';

/** The icon each action draws. `drop` and `remove` both take something away. */
export const ACTION_ICON: Record<RowAction, IconName> = {
  pin: 'pin',
  remove: 'x',
  restore: 'plus',
  add: 'plus',
  up: 'up',
  down: 'down',
  drop: 'x'
};

/**
 * A row button's accessible name. Every one of them names the model, because
 * a screen reader hears a row's buttons out of the row's context: "Never ship
 * Opus" is an action, "Never ship" alone is a guess about which row.
 */
export function actionLabel(action: RowAction, name: string, pressed = false): string {
  switch (action) {
    case 'pin':
      return pressed ? `Unpin ${name}` : `Pin ${name}`;
    case 'remove':
      return `Never ship ${name}`;
    case 'restore':
      return `Restore ${name}`;
    case 'add':
      return `Add ${name}`;
    case 'up':
      return `Move ${name} up`;
    case 'down':
      return `Move ${name} down`;
    case 'drop':
      return `Take ${name} off the list`;
  }
}

/** The five lists, in the order section 6.6 draws them. */
export interface Sections {
  lineup: DisplayRow[];
  next: DisplayRow[];
  leaving: DisplayRow[];
  blocked: DisplayRow[];
  removed: DisplayRow[];
  missing: DisplayRow[];
}

const EMPTY: Sections = { lineup: [], next: [], leaving: [], blocked: [], removed: [], missing: [] };

function ranked(row: Listed, rank: string, note: string): RankedRow {
  return { kind: 'ranked', id: row.id, name: row.name, rank, note, row };
}

function bare(id: string, name: string, why: BareRow['why'], note: string): BareRow {
  return { kind: 'bare', id, name, rank: '', note, why };
}

/** Every id a section shows, so a row is never drawn twice. */
export function idsOf(sections: Sections): string[] {
  return [
    ...sections.lineup,
    ...sections.next,
    ...sections.leaving,
    ...sections.blocked,
    ...sections.removed,
    ...sections.missing
  ].map((row) => row.id);
}

/**
 * The five lists, ranked and named.
 *
 * `alsoVisible` is what the pane is showing somewhere else -- the hand list in
 * manual mode, the pool in the all-reachable view. A leaving id is drawn only
 * when it is in none of them: finding 10 is explicit that the deduplication is
 * against every displayed collection, not only `next`, or a model that ships
 * today and would not tomorrow is shown twice and read as two changes.
 *
 * A leaving id that the pool still holds is a ranked row -- its score is known,
 * so it is drawn with it -- and only one the pool has never heard of becomes a
 * bare row, named by its id because that is all anybody knows.
 */
export function tableSections(
  preview: PreviewResult | null,
  diff: LineupDiff,
  alsoVisible: readonly string[] = []
): Sections {
  if (!preview) return EMPTY;

  const lineup = preview.models.map((row, at) => ranked(row, `${at + 1}`, ''));
  const next = preview.next.map((row, at) => ranked(row, `${preview.models.length + at + 1}`, ''));
  const blocked = (preview.blocked ?? []).map((row) => ranked(row, '', ''));
  const removed = (preview.removed ?? []).map((row) => ranked(row, '', ''));
  const missing = (preview.missing ?? []).map((row) => bare(row.id, row.name, 'missing', ''));

  const shown = new Set([
    ...lineup.map((row) => row.id),
    ...next.map((row) => row.id),
    ...blocked.map((row) => row.id),
    ...removed.map((row) => row.id),
    ...missing.map((row) => row.id),
    ...alsoVisible
  ]);

  const pool = new Map((preview.pool ?? []).map((row) => [row.id, row]));
  const leaving = diff.leaving
    .filter((id) => !shown.has(id))
    .map((id) => {
      const held = pool.get(id);
      return held ? ranked(held, '', 'leaves') : bare(id, id, 'leaving', 'leaves');
    });

  return { lineup, next, leaving, blocked, removed, missing };
}

/**
 * What an empty lineup says.
 *
 * "Nothing ships: no reachable model meets every need" is a claim about needs,
 * and it is only allowed when the answer actually says so: the server's
 * `failed_needs` is non-zero, nothing was removed by hand, and the seat is not
 * in manual mode -- where an empty hand list is the whole explanation. Every
 * other empty lineup gets the neutral sentence (finding 10).
 */
export function emptyText(preview: PreviewResult | null, mode: ProfileMode): string {
  const neutral = 'Nothing would ship with these settings.';
  if (!preview) return neutral;
  if (mode === 'manual') return neutral;
  if ((preview.removed ?? []).length > 0) return neutral;
  if ((preview.failed_needs ?? 0) > 0) {
    return 'Nothing ships: no reachable model meets every need.';
  }
  return neutral;
}

/** The words after a pool row's name: where it stands, in the current draft. */
export interface StandingInput {
  /** the lineup's ids, in order */
  lineup: readonly string[];
  pinned: readonly string[];
  removed: readonly string[];
  needs: readonly Need[];
  /** the needs the pool row is not known to meet */
  lacks: readonly Need[];
}

/**
 * `standing`, ported from the profile page it replaces (section 6.6 keeps its
 * wording, including the hard-coded "fails Must support": the row is in a seat
 * whose first need is what the server reports as unmet).
 */
export function standing(id: string, at: StandingInput): string {
  if (at.removed.includes(id)) return 'removed';
  const rank = at.lineup.indexOf(id);
  if (rank >= 0) return at.pinned.includes(id) ? `pinned · ships #${rank + 1}` : `ships #${rank + 1}`;
  if (at.pinned.includes(id)) return 'pinned · skipped';
  if (at.needs.length && at.lacks.length) return 'fails Must support';
  return 'not shipping';
}

/** The diff's move for a row, or null when there is nothing to say. */
export function moveFor(diff: LineupDiff, id: string): RowMove | null {
  const move = diff.moves[id];
  return move && move.text ? move : null;
}

/** What the "can do" column holds. */
export interface CanDo {
  text: string;
  /** a required need nobody has answered for: the text says so in `--c-warn` */
  warn: boolean;
  /** why the cell is a dash, when it is one; empty when the text speaks */
  title: string;
}

/**
 * The needs this model is known to do, and the required ones nobody answered
 * for. An explicit `null` is the server saying "no source said", so a required
 * need in that state is written as `<tag> unknown` rather than left out; a
 * missing field is an old server and is not turned into an answer (rule 2) --
 * which is why an empty cell says which of the two absences it is.
 */
export function canDo(row: Listed, needs: readonly Need[]): CanDo {
  const parts: string[] = [];
  let warn = false;
  for (const need of NEEDS) {
    const answer = row.abilities?.[need];
    if (answer === true) parts.push(NEED_TAG[need]);
    else if (answer === null && needs.includes(need)) {
      parts.push(`${NEED_TAG[need]} unknown`);
      warn = true;
    }
  }
  if (row.abilities === undefined) {
    return { text: '', warn, title: 'the server does not report capabilities' };
  }
  if (!parts.length) {
    return { text: '', warn, title: 'no source says it can do any of these' };
  }
  return { text: parts.join(' · '), warn, title: '' };
}

/** The routers serving a model, as the pool row reports them: `or/`, two at most. */
export function via(localIds: readonly string[]): string {
  const prefixes: string[] = [];
  for (const id of localIds) {
    const cut = id.indexOf('/');
    const prefix = cut > 0 ? id.slice(0, cut + 1) : id;
    if (prefix && !prefixes.includes(prefix)) prefixes.push(prefix);
  }
  if (prefixes.length <= 2) return prefixes.join(' ');
  return `${prefixes.slice(0, 2).join(' ')} +${prefixes.length - 2}`;
}

/** How many rows the all-reachable view draws before it asks for a narrower search. */
export const POOL_LIMIT = 200;

export interface PoolFilter {
  /** what was typed into the filter, matched against name, id and local ids */
  needle?: string;
  unscoredOnly?: boolean;
  needs?: readonly Need[];
  /**
   * The seat's mode. A need filters the pool in manual mode, where a model that
   * cannot do the job cannot be added to the hand list at all; in auto mode the
   * pool keeps it, because "fails Must support" is something to be able to see.
   */
  mode?: ProfileMode;
  limit?: number;
}

export interface PoolView {
  rows: Listed[];
  /** how many matched but were not drawn */
  more: number;
}

/** The pool, filtered the way the all-reachable view asks for. */
export function poolRows(pool: readonly Listed[] | null | undefined, filter: PoolFilter = {}): PoolView {
  const needle = (filter.needle ?? '').trim().toLowerCase();
  const needs = filter.needs ?? [];
  const limit = filter.limit ?? POOL_LIMIT;

  const matched = (pool ?? []).filter((row) => {
    if (filter.unscoredOnly && row.scored !== false) return false;
    if (filter.mode === 'manual' && needs.length && !needs.every((need) => row.abilities?.[need] === true)) {
      return false;
    }
    if (!needle) return true;
    return (
      row.name.toLowerCase().includes(needle) ||
      row.id.toLowerCase().includes(needle) ||
      row.local_ids.some((id) => id.toLowerCase().includes(needle))
    );
  });

  return { rows: matched.slice(0, limit), more: Math.max(0, matched.length - limit) };
}

/** The router ids that matched no catalogue entry, matched the same way. */
export function unlinkedRows(
  unlinked: readonly { local_id: string; name: string }[] | null | undefined,
  filter: { needle?: string; unscoredOnly?: boolean; limit?: number } = {}
): { local_id: string; name: string }[] {
  const needle = (filter.needle ?? '').trim().toLowerCase();
  // As on the page this replaces: an unlinked id is noise until somebody is
  // either looking for something or asking for the unscored ones.
  if (!needle && !filter.unscoredOnly) return [];
  return (unlinked ?? [])
    .filter((row) => !needle || row.local_id.toLowerCase().includes(needle))
    .slice(0, filter.limit ?? 12);
}

/** The "Unscored only · N" count: pool rows with no score, plus unlinked ids. */
export function unscoredCount(preview: PreviewResult | null): number {
  if (!preview) return 0;
  return (preview.pool ?? []).filter((row) => row.scored === false).length + (preview.unlinked ?? []).length;
}

/**
 * The index ↑/↓ lands on. Clamped rather than wrapped: running off the end of a
 * list back to its top moves the selection somewhere the eye did not follow.
 */
export function stepIndex(from: number, count: number, by: number): number {
  if (count <= 0) return -1;
  if (from < 0) return by > 0 ? 0 : count - 1;
  return Math.max(0, Math.min(count - 1, from + by));
}

/**
 * The row that holds the single tab stop of a roving `tabindex`: the one that
 * was just moved to, else the selected row, else the first.
 */
export function tabStop(
  ids: readonly string[],
  stop: string | null,
  selected: string | null
): string | null {
  if (stop && ids.includes(stop)) return stop;
  if (selected && ids.includes(selected)) return selected;
  return ids[0] ?? null;
}
