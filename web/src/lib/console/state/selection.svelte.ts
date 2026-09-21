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

  /**
   * Whether somebody asked for this model, as opposed to the seat answering
   * with one. Below 1280px the inspector is an overlay (section 6.8) and an
   * overlay that opens itself the moment a seat's preview lands would cover the
   * seat on every load, so the panel follows this flag: `select` raises it,
   * `adopt` never does, and `close` lowers it without forgetting the id.
   */
  wanted = $state(false);

  private deps: SelectionDeps;

  constructor(deps: SelectionDeps) {
    this.deps = deps;
    this.id = deps.read();
    // A `?model=` in the URL was put there by somebody: the link that carried
    // it, or the select that wrote it. Only `adopt` picks a model nobody asked
    // for, and it leaves this alone.
    this.wanted = this.id !== null;
  }

  /** Pick a model, or none. Writing is what makes it survive a reload. */
  select(id: string | null): void {
    this.wanted = id !== null;
    if (id === this.id) return;
    this.id = id;
    this.deps.replace(id);
  }

  /** Close the inspector without forgetting which model it was about. */
  close(): void {
    this.wanted = false;
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
    // The seat's own choice, not a person's: no `wanted`, so an overlay
    // (section 6.8) does not open itself on every load.
    const chosen = moved ?? lineup[0] ?? null;
    // But when the address bar *did* name a model and the pool no longer has
    // it, the choice above is a correction: `?model=gone` is a link to nothing,
    // and leaving it there would hand the next reload or share a different
    // model than the one on screen. An address bar that said nothing is left
    // saying nothing -- a click is the only thing that puts `?model=` in it.
    const was = this.id;
    this.id = chosen;
    if (was !== null) this.deps.replace(chosen);
  }
}
