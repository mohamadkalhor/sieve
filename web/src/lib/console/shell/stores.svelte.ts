/**
 * The stores the shell runs on, assembled in one place (CONSOLE.md section 5.4).
 *
 * Package A left three thin bodies here so that the layout, the shell and the
 * routes could compile while `state/*.svelte.ts` was still unwritten; this is
 * the package that replaces them with the real classes. What is assembled here
 * is also the only place where the stores are wired to each other and to the app
 * they live in: the seats list holds the one answer the pane, the palette and
 * the routes all read, the status store is handed the two things it cannot
 * import (`/v1/me` adoption and the run pulse), and the palette is given the
 * context its providers read.
 *
 * `goto` comes from `$app/navigation` rather than from the caller: the palette
 * navigates, and a route-level callback would make the shell depend on whoever
 * mounted it. Tests never call this function -- they build the classes with
 * three lines of their own (REVIEW.md finding 4).
 */
import { goto } from '$app/navigation';
import { api } from '$lib/api/client';
import { runPulse } from '$lib/refresh.svelte';
import { session } from '$lib/session.svelte';
import { PROVIDERS } from '$lib/console/palette/providers';
import { Palette } from '$lib/console/state/palette.svelte';
import { SeatsStore } from '$lib/console/state/seats.svelte';
import { StatusStore } from '$lib/console/state/status.svelte';
import type { PaletteContext, SelectionLike } from '$lib/console/contracts';

export interface ShellStores {
  seats: SeatsStore;
  status: StatusStore;
  palette: Palette;
}

/**
 * What the palette reads when no seat is open.
 *
 * Nothing asks it anything -- the providers that use the selection are the ones
 * that need a seat, and they answer with nothing when there is none -- but
 * `PaletteContext` names the field, so it has to be something rather than
 * `undefined`.
 */
const NO_SELECTION: SelectionLike = {
  id: null,
  select: () => {},
  adopt: () => {}
};

/** Fresh stores per mount: a test or an HMR reload must not inherit a timer. */
export function shellStores(): ShellStores {
  const seats = new SeatsStore({ api });

  // the layout hands the status store the two things it must not know: how a
  // person gets signed in on this server, and what to bump when a run ends
  const status = new StatusStore({
    adopt: (row) => session.adopt(row),
    pulse: runPulse
  });

  const palette = new Palette({
    providers: PROVIDERS,
    ctx: (): PaletteContext => ({
      goto: (href) => goto(href),
      seats,
      status,
      selection: palette.here?.selection ?? NO_SELECTION,
      session: () => palette.here?.session() ?? null,
      inspect: (id, modality) => palette.here?.inspect(id, modality)
    })
  });

  return { seats, status, palette };
}
