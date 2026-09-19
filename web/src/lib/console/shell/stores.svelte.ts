/**
 * The stores the shell needs, as one seam (CONSOLE.md section 5.4, REVIEW.md
 * finding 4: "wire `(app)/+layout.svelte` through factories, not through
 * classes that do not exist yet").
 *
 * The layout calls `shellStores()` once and hands the three stores down with
 * `setContext`, so no screen imports a module singleton and every test can
 * build its own. Package F lands `state/seats.svelte.ts`,
 * `state/status.svelte.ts` and `state/palette.svelte.ts`; when it does, the
 * three bodies below return those classes and nothing else in the app changes.
 *
 * Until then these are thin, not fake:
 *
 * - `seats` really fetches the list, because `/seats` has to answer "which
 *   seat?" from real rows and a ship has to be able to show a new count
 *   without a reload. It does not filter, sort or remember anything -- that is
 *   F's store, and inventing a second copy of it here would only be thrown
 *   away twice.
 * - `status` does not poll. Starting a timer before there is anything to draw
 *   from it is a request nobody reads.
 * - `palette` opens, moves and closes, over an empty registry: the commands
 *   come from C's registry through F's store, and until they land the palette
 *   says it has none.
 */
import { api } from '$lib/api/client';
import type { ApiError, SeatRow } from '$lib/api/client';
import type {
  CommandLike,
  PaletteLike,
  SeatsStoreLike,
  StatusStoreLike
} from '$lib/console/contracts';

export interface ShellStores {
  seats: SeatsStoreLike;
  status: StatusStoreLike;
  palette: PaletteLike;
}

/** The seat `/seats` opens on a desktop: the list, and nothing else. */
function thinSeats(): SeatsStoreLike {
  const state = $state({
    rows: null as SeatRow[] | null,
    error: null as ApiError | null,
    loading: false,
    degraded: false
  });

  return {
    get rows() {
      return state.rows;
    },
    get error() {
      return state.error;
    },
    get loading() {
      return state.loading;
    },
    get degraded() {
      return state.degraded;
    },
    async load(): Promise<void> {
      state.loading = true;
      const result = await api.seats();
      if (result.ok) {
        state.rows = result.value.rows;
        state.degraded = result.value.degraded;
        state.error = null;
      } else {
        state.rows = null;
        state.degraded = false;
        state.error = result.error;
      }
      state.loading = false;
    },
    patch(name: string, part: Partial<SeatRow>): void {
      if (!state.rows) return;
      state.rows = state.rows.map((row) => (row.name === name ? { ...row, ...part } : row));
    }
  };
}

function quietStatus(): StatusStoreLike {
  return {
    row: null,
    unreachable: false,
    start(): () => void {
      // no polling yet: a status bar that polls before it can draw anything
      // is a request nobody reads
      return () => {};
    }
  };
}

function emptyPalette(): PaletteLike {
  const state = $state({
    open: false,
    query: '',
    active: -1,
    results: [] as CommandLike[]
  });

  /** The active row is only meaningful while it points at something. */
  const settle = () => {
    state.active = state.results.length ? Math.min(state.active, state.results.length - 1) : -1;
    if (state.active < 0 && state.results.length) state.active = 0;
  };

  return {
    get open() {
      return state.open;
    },
    set open(next: boolean) {
      state.open = next;
      if (!next) state.query = '';
    },
    get query() {
      return state.query;
    },
    set query(next: string) {
      state.query = next;
      settle();
    },
    get results() {
      return state.results;
    },
    get active() {
      return state.active;
    },
    set active(next: number) {
      state.active = next;
    },
    toggle(): void {
      state.open = !state.open;
      if (!state.open) state.query = '';
    },
    move(by: number): void {
      const count = state.results.length;
      if (!count) return;
      state.active = Math.max(0, Math.min(count - 1, (state.active < 0 ? 0 : state.active) + by));
    },
    run(): void {
      const chosen = state.results[state.active];
      if (!chosen) return;
      state.open = false;
      void chosen.run();
    }
  };
}

/** Fresh stores per mount: the tests need their own (CONSOLE.md section 5.4). */
export function shellStores(): ShellStores {
  return { seats: thinSeats(), status: quietStatus(), palette: emptyPalette() };
}
