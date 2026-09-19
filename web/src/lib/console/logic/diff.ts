/**
 * Two lineups side by side (CONSOLE.md section 5.2, REVIEW.md finding 5).
 *
 * A seat has two orders: what the connector holds (`live`) and what the saved
 * settings would ship (`lineup`). Either may be `null` -- nobody stored a chain,
 * nothing has been saved -- and then there is no comparison to make, which is
 * not the same answer as "they match". When either side is missing this module
 * reports `known: false` and makes no claim about any row: no "new" badges,
 * no "up 2"s, no changes count.
 *
 * `changes` is the same arithmetic the seats list does on the server
 * (`sieve/scoring/select.py`, `changes`): added + removed + moved, by id. Both
 * sides assert against one fixture, `tests/fixtures/lineup_diff.json`. It is
 * *not* the same question as `in_step`: two lineups that hold the same ids in
 * the same order are in step, and anything else this counts. "No badge" and
 * "0 changes" are different statements, so a caller that wants the badge
 * compares the ids itself.
 *
 * A lineup holds no id twice -- `select` never seats one twice, and a chain
 * holds what it shipped once -- so "where it sits" has one answer per id.
 */
export type MoveKind = 'same' | 'up' | 'down' | 'new';

/** What happened to one id between the two orders. */
export interface RowMove {
  id: string;
  kind: MoveKind;
  /** places moved, positive upward; 0 for `same` and `new` */
  by: number;
  /** the badge text: `up 2`, `down 1`, `new`, or nothing at all */
  text: '' | `up ${number}` | `down ${number}` | 'new';
}

export interface LineupDiff {
  /** only ids in the lineup: how each of them stands to the live chain */
  moves: Record<string, RowMove>;
  /** ids the live chain holds and the lineup does not */
  leaving: string[];
  /** added + removed + moved; null when either side is null */
  changes: number | null;
  /** both sides were read, so every field above means something */
  known: boolean;
}

function changesOf(live: readonly string[], lineup: readonly string[]): number {
  const before = new Set(live);
  const now = new Set(lineup);
  let count = 0;
  for (const id of before) if (!now.has(id)) count += 1;
  for (const id of now) if (!before.has(id)) count += 1;
  const wasAt = new Map<string, number>();
  live.forEach((id, index) => {
    if (!wasAt.has(id)) wasAt.set(id, index);
  });
  lineup.forEach((id, index) => {
    const at = wasAt.get(id);
    if (at !== undefined && at !== index) count += 1;
  });
  return count;
}

export function diffLineup(
  live: readonly string[] | null,
  lineup: readonly string[] | null
): LineupDiff {
  if (live === null || lineup === null) {
    return { moves: {}, leaving: [], changes: null, known: false };
  }

  const wasAt = new Map<string, number>();
  live.forEach((id, index) => {
    if (!wasAt.has(id)) wasAt.set(id, index);
  });
  const isNow = new Set(lineup);

  const moves: Record<string, RowMove> = {};
  lineup.forEach((id, index) => {
    const before = wasAt.get(id);
    if (before === undefined) {
      moves[id] = { id, kind: 'new', by: 0, text: 'new' };
      return;
    }
    const by = before - index;
    if (by > 0) moves[id] = { id, kind: 'up', by, text: `up ${by}` };
    else if (by < 0) moves[id] = { id, kind: 'down', by: -by, text: `down ${-by}` };
    else moves[id] = { id, kind: 'same', by: 0, text: '' };
  });

  return {
    moves,
    leaving: live.filter((id) => !isNow.has(id)),
    changes: changesOf(live, lineup),
    known: true
  };
}

/**
 * The Ship now button's words.
 *
 * `changes` names what would happen, so the button can say how much rather than
 * only that something would. The button's own disabled state (and its reason,
 * "this is already what ships") comes first: a disabled button that said
 * "Ship 3 changes" about a change it would not make would be a lie. An unknown
 * or zero difference adds nothing, because there is nothing to name.
 */
export function shipLabel(
  diff: LineupDiff,
  state: { disabled: boolean; label: string }
): string {
  if (state.disabled) return state.label;
  if (!diff.known || diff.changes === null || diff.changes <= 0) return state.label;
  return diff.changes === 1 ? 'Ship 1 change' : `Ship ${diff.changes} changes`;
}
