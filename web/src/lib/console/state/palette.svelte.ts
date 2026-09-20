/**
 * The command palette's state (CONSOLE.md sections 5.3 and 6.1).
 *
 * `results` is derived, never stored: the providers are asked again whenever the
 * query changes and whenever anything a provider read changes, so a seat that
 * has just appeared is in the list without the palette being told about it.
 * Nothing here fetches -- a provider that needs a number reads a store
 * (section 5.2).
 *
 * The open seat is *attached* rather than looked up: the palette is built by the
 * layout, and the object it needs lives in the route that owns the session, one
 * level below. `attachSeat` is the seam, and it is deliberately the only thing
 * that reaches across (REVIEW.md finding 4).
 */
import { match, type Command, type Provider } from '../logic/commands';
import type {
  CommandLike,
  PaletteContext,
  PaletteLike,
  SeatSessionLike,
  SelectionLike
} from '../contracts';
import type { Modality } from '$lib/types';

/** The seat the route has open: what the providers that need one read. */
export interface SeatHere {
  selection: SelectionLike;
  session(): SeatSessionLike | null;
  inspect(id: string, modality: Modality): void;
}

export class Palette implements PaletteLike {
  /**
   * The open seat, set by the route that has one. A `$state` field because the
   * providers read it: a seat that opens has to change the list.
   */
  here = $state<SeatHere | null>(null);

  #open = $state(false);
  #query = $state('');
  #active = $state(0);
  #providers: readonly Provider[];
  #ctx: () => PaletteContext;

  constructor(deps: { providers: readonly Provider[]; ctx: () => PaletteContext }) {
    this.#providers = deps.providers;
    this.#ctx = deps.ctx;
  }

  get open(): boolean {
    return this.#open;
  }

  set open(next: boolean) {
    this.#open = next;
    // a palette that opens showing the last search is a palette that lies about
    // what the next Enter will run
    if (!next) {
      this.#query = '';
      this.#active = 0;
    }
  }

  get query(): string {
    return this.#query;
  }

  set query(next: string) {
    if (next === this.#query) return;
    this.#query = next;
    // a new query is a new list, and its first row is the best match
    this.#active = 0;
  }

  /** Every command the providers offer for the stores as they are now. */
  get all(): Command[] {
    return this.#providers.flatMap((provider) => provider(this.#ctx()));
  }

  results: readonly CommandLike[] = $derived(match(this.query, this.all));

  /** Index into `results`; -1 when there is nothing to run. */
  get active(): number {
    return this.results.length ? Math.min(this.#active, this.results.length - 1) : -1;
  }

  set active(next: number) {
    this.#active = next;
  }

  toggle(): void {
    this.open = !this.open;
  }

  /** ↑/↓, wrapping: a jump-list you cannot leave by holding a key is a trap. */
  move(by: number): void {
    const count = this.results.length;
    if (!count) return;
    const from = this.active < 0 ? 0 : this.active;
    this.#active = (from + by + count) % count;
  }

  run(): void {
    const chosen = this.results[this.active];
    if (!chosen || chosen.enabled === false) return;
    this.open = false;
    void chosen.run();
  }
}

/**
 * Hand the palette the seat the route has open.
 *
 * Called by the seat route, which is the only place that knows the session and
 * the selection; `null` when it is left, so a command never runs against a seat
 * that is no longer on screen.
 */
export function attachSeat(palette: PaletteLike, here: SeatHere | null): void {
  if (palette instanceof Palette) palette.here = here;
}
