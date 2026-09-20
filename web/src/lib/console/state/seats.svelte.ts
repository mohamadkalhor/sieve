/**
 * The seats list, held once for the whole app (CONSOLE.md sections 5.3, 6.2).
 *
 * One request answers every question the pane has: which seats there are, what
 * each one is called, whether it is in step and how far out of it. Nothing here
 * fetches per row -- the pane's acceptance is that the list is painted by a
 * single call, and a row that fetched its own detail would spend that call N
 * times.
 *
 * `deps.api` is injected rather than imported (REVIEW.md finding 4): the app
 * passes the real client, a test passes three lines.
 *
 * `load()` is safe to call from two places at once -- the layout's `$effect`
 * and the seat route both want the list -- and the second caller joins the
 * request already in flight instead of starting another one. That is what makes
 * "one request paints the seats pane" true rather than lucky.
 */
import type { ApiError, Result, SeatRow, SeatsResult } from '$lib/api/client';
import type { SeatsStoreLike } from '../contracts';

/** The one route this store reads. */
export interface SeatsApi {
  seats(): Promise<Result<SeatsResult>>;
}

export class SeatsStore implements SeatsStoreLike {
  /** null until an answer arrives: "not asked yet" is not "none" */
  rows = $state<SeatRow[] | null>(null);
  error = $state<ApiError | null>(null);
  loading = $state(false);
  /** the fallback path was taken: rows, but no lineup, so no in-step claims */
  degraded = $state(false);

  #api: SeatsApi;
  /** the request in flight, so two callers cannot become two requests */
  #inflight: Promise<void> | null = null;

  constructor(deps: { api: SeatsApi }) {
    this.#api = deps.api;
  }

  load(): Promise<void> {
    if (this.#inflight) return this.#inflight;
    const run = this.#read();
    this.#inflight = run;
    return run;
  }

  async #read(): Promise<void> {
    this.loading = true;
    const result = await this.#api.seats();
    if (result.ok) {
      this.rows = result.value.rows;
      this.degraded = result.value.degraded;
      this.error = null;
    } else {
      // a failed read is not an empty list: `rows` goes back to "unknown" so
      // the pane shows the error and never a list of nothing
      this.rows = null;
      this.degraded = false;
      this.error = result.error;
    }
    this.loading = false;
    this.#inflight = null;
  }

  /**
   * After a ship: the count and the chain moved, and the pane says so without
   * a second request. Only the row that was shipped changes.
   */
  patch(name: string, part: Partial<SeatRow>): void {
    if (!this.rows) return;
    this.rows = this.rows.map((row) => (row.name === name ? { ...row, ...part } : row));
  }
}
