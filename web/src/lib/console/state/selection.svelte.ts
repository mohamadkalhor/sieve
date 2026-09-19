/**
 * Which model the inspector is about (CONSOLE.md section 5.3).
 *
 * The selection is URL state, not component state: `?model=<id>` survives a
 * reload, is worth sharing, and lets the palette open a model in the inspector
 * without either side knowing about the other. So this class owns the id and
 * nothing else. The route lends it the two moves it may make -- reading
 * `?model=` and replacing it -- and no component ever touches `window`.
 *
 * `adopt` is the half that matters after a ship: the lineup changes underneath
 * the selection, and a model that left it must not stay selected. It picks the
 * `?model=` id when it is still in the pool, otherwise the first row that moved,
 * otherwise the first row, otherwise nothing -- and never claims a selection
 * before an answer has arrived to check it against.
 */
import type { SelectionLike, SeatSessionLike } from '../contracts';
import { diffLineup } from '../logic/diff';

/** The two URL moves the route lends the selection. */
export interface SelectionDeps {
  /** `?model=` as the address bar has it now */
  read(): string | null;
  /** write `?model=`; null clears it, and neither pushes history */
  replace(id: string | null): void;
}

export class Selection implements SelectionLike {
  /** the inspected model id, mirrored to `?model=` */
  id = $state<string | null>(null);

  private deps: SelectionDeps;

  constructor(deps: SelectionDeps) {
    this.deps = deps;
    this.id = deps.read();
  }

  /** Pick a model, or none. Writing is what makes it survive a reload. */
  select(id: string | null): void {
    if (id === this.id) return;
    this.id = id;
    this.deps.replace(id);
  }

  /** Keep the selection honest against the pool the seat just answered with. */
  adopt(session: SeatSessionLike): void {
    const preview = session.preview;
    // Nothing has answered yet. A `?model=` that is about to be checked is not a
    // selection to throw away, and there is no pool to check it against.
    if (!preview) return;

    const pool = preview.pool ?? [];
    if (this.id !== null && pool.some((row) => row.id === this.id)) return;

    const lineup = preview.models.map((row) => row.id);
    const { moves } = diffLineup(session.live, lineup);
    const moved = lineup.find((id) => moves[id] && moves[id].kind !== 'same');
    this.select(moved ?? lineup[0] ?? null);
  }
}
