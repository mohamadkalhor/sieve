/**
 * The console's context keys (CONSOLE.md section 5.4).
 *
 * The stores are created once, in `(app)/+layout.svelte`, and handed down:
 * `session` and `runPulse` stay the module singletons they already were, the
 * new ones are per-mount so a test can have a fresh one. That is why the keys
 * live here and not next to the classes -- the layout has to name a key it
 * cannot otherwise see, and a component deep in the tree has to read it
 * without importing the layout.
 *
 * `get` throws when nothing was provided. A missing provider is a wiring bug,
 * and a silent `undefined` would show up much later as an empty pane that
 * looks like a quiet server.
 */
import { getContext, setContext } from 'svelte';
import type {
  CardCacheLike,
  PaletteLike,
  SeatSessionLike,
  SeatsStoreLike,
  SelectionLike,
  StatusStoreLike
} from './contracts';

/** Per-context-run, so two mounts of the app never share a key. */
const KEYS = {
  seats: Symbol('sieve.console.seats'),
  status: Symbol('sieve.console.status'),
  palette: Symbol('sieve.console.palette'),
  selection: Symbol('sieve.console.selection'),
  cards: Symbol('sieve.console.cards'),
  session: Symbol('sieve.console.seat-session')
} as const;

function provide<T>(key: symbol, value: T, what: string): T {
  // Catching a missing store here rather than in `read` matters: the layout
  // that forgot is still on the stack, and the message names which one it was.
  if (value === undefined || value === null) {
    throw new Error(`${what} was neither provided nor there to provide`);
  }
  setContext(key, value);
  return value;
}

function read<T>(key: symbol, what: string): T {
  const value = getContext<T | undefined>(key);
  if (value === undefined) {
    throw new Error(`${what} was never provided: (app)/+layout.svelte must set it during init`);
  }
  return value;
}

/* -- provided by the layout, read by the shell and the panes ---------------- */

export const provideSeats = (store: SeatsStoreLike): SeatsStoreLike =>
  provide(KEYS.seats, store, 'the seats store');
export const seats = (): SeatsStoreLike => read<SeatsStoreLike>(KEYS.seats, 'the seats store');

export const provideStatus = (store: StatusStoreLike): StatusStoreLike =>
  provide(KEYS.status, store, 'the status store');
export const status = (): StatusStoreLike => read<StatusStoreLike>(KEYS.status, 'the status store');

export const providePalette = (palette: PaletteLike): PaletteLike =>
  provide(KEYS.palette, palette, 'the palette');
export const palette = (): PaletteLike => read<PaletteLike>(KEYS.palette, 'the palette');

/* -- provided by the seat route, read by the inspector ---------------------- */

export const provideSelection = (selection: SelectionLike): SelectionLike =>
  provide(KEYS.selection, selection, 'the selection');
export const selection = (): SelectionLike => read<SelectionLike>(KEYS.selection, 'the selection');

export const provideCards = (cards: CardCacheLike): CardCacheLike =>
  provide(KEYS.cards, cards, 'the model-card cache');
export const cards = (): CardCacheLike => read<CardCacheLike>(KEYS.cards, 'the model-card cache');

export const provideSeatSession = (session: SeatSessionLike): SeatSessionLike =>
  provide(KEYS.session, session, 'the seat session');
/** Optional on purpose: the shell renders on routes where no seat is open. */
export const seatSession = (): SeatSessionLike | null =>
  getContext<SeatSessionLike | undefined>(KEYS.session) ?? null;
