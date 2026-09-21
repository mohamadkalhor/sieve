/**
 * The seams between the console's packages (REVIEW.md finding 4).
 *
 * Packages land one after another, and each one has to compile while the next
 * one's files are still missing. So every crossing between them is named here
 * as a *structural* interface -- what a thing can do, not which class it is --
 * and nobody imports another package's class to write a type down. The real
 * classes still land (`SeatsStore` in `state/seats.svelte.ts` and so on), they
 * simply have to satisfy these, and a test can pass a three-line stub.
 *
 * `(app)/+layout.svelte` therefore wires the app through factories rather than
 * through classes that do not exist yet. C may append to this file; nobody has
 * to edit what is already written here to add a package.
 */
import type { Modality, Profile } from '$lib/types';
import type { Chain } from '$lib/types';
import type {
  ApiError,
  AxisRow,
  HistoryRow,
  ModelCard,
  Need,
  PreviewResult,
  ProfileMode,
  SeatRow,
  StatusRow
} from '$lib/api/client';

/** `CardCache.get` says which of four things a card read is. */
export type CardState = 'loading' | 'ready' | 'fallback' | 'error';

/**
 * `logic/settings.ts` `Settings`, as the shell and the inspector read it.
 *
 * Duplicated rather than imported on purpose: `logic/` is package C's, and a
 * type import from a file that does not exist yet would stop every other
 * package from compiling. Keep the two in step by hand; the field names are
 * the contract.
 */
export interface SettingsLike {
  weights: Record<string, number>;
  /** axis order as added; never re-sorted by weight */
  order: string[];
  /** page-only locks, persisted per seat in localStorage */
  locked: string[];
  ship: number;
  mode: ProfileMode;
  manual: string[];
  pinned: string[];
  removed: string[];
  needs: Need[];
  prefixWeights: Record<string, number>;
}

/** What `shipState` decides: the label, whether it is pressable, and why not. */
export interface ShipStateLike {
  label: string;
  disabled: boolean;
  title: string;
}

/** `state/seats.svelte.ts` `SeatsStore`. */
export interface SeatsStoreLike {
  /** null until an answer arrives: "not asked yet" is not "none" */
  rows: SeatRow[] | null;
  error: ApiError | null;
  loading: boolean;
  /** the fallback path was taken: no lineup data, so no in-step claims */
  degraded: boolean;
  load(): Promise<void>;
  /** after a ship, so the pane updates without a refetch */
  patch(name: string, part: Partial<SeatRow>): void;
}

/** `state/status.svelte.ts` `StatusStore`. */
export interface StatusStoreLike {
  row: StatusRow | null;
  unreachable: boolean;
  /** the last read that failed, so the bar can name it instead of drawing nothing */
  failed: ApiError | null;
  /** begins polling and returns the function that stops it */
  start(): () => void;
}

/** `logic/commands.ts` `Command`, as the palette and its providers use it. */
export interface CommandLike {
  id: string;
  group: 'Seats' | 'Models' | 'Go to' | 'Actions';
  title: string;
  hint?: string;
  keywords?: string;
  run(): void | Promise<void>;
  enabled?: boolean;
}

/** `state/palette.svelte.ts` `Palette`. */
export interface PaletteLike {
  open: boolean;
  query: string;
  /** derived from `query` -- a getter is fine, nothing outside assigns it */
  readonly results: readonly CommandLike[];
  /** index into `results`; -1 when there is nothing to run */
  active: number;
  toggle(): void;
  move(by: number): void;
  run(): void;
}

/** `state/selection.svelte.ts` `Selection`. */
export interface SelectionLike {
  /** the inspected model, mirrored to `?model=` */
  id: string | null;
  select(id: string | null): void;
  /** when the URL names none: first moved row, else the first row, else null */
  adopt(session: SeatSessionLike): void;
}

/** `state/card.svelte.ts` `CardCache`. */
export interface CardCacheLike {
  get(id: string, modality: Modality): { card: ModelCard | null; state: CardState };
}

/**
 * `state/seat.svelte.ts` `SeatSession`, read-only except for the edits the
 * inspector is allowed to make (pin and remove, through `edit`).
 */
export interface SeatSessionLike {
  readonly name: string;
  readonly profile: Profile | null;
  readonly chain: Chain | null;
  readonly everyAxis: AxisRow[];
  readonly prefixes: string[];
  readonly settings: SettingsLike | null;
  readonly preview: PreviewResult | null;
  readonly loading: boolean;
  /** an answer is in flight long enough to say so */
  readonly pending: boolean;
  readonly failed: string | null;
  readonly shipping: boolean;
  readonly said: { ok: boolean; text: string } | null;
  /** 404/409 on the seat itself: the page shows this instead of a pane */
  readonly gone: ApiError | null;
  /** the ids the connector holds now, or null when nothing read it */
  readonly live: string[] | null;
  readonly labels: Record<string, string>;
  readonly meanings: Record<string, string>;
  readonly spare: AxisRow[];
  readonly button: ShipStateLike;
  readonly shipText: string;
  open(): Promise<void>;
  close(): void;
  edit(next: SettingsLike | { error: string }): void;
  ship(): Promise<void>;
  linkThen(localId: string, then: (id: string) => SettingsLike): Promise<void>;
  copy(to: string): Promise<string | null>;
  rename(to: string): Promise<string | null>;
  destroy(): Promise<boolean>;
  history(): Promise<HistoryRow[]>;
}

/**
 * What a command provider is handed: the stores it reads and the two moves it
 * may make. No provider fetches anything (CONSOLE.md section 5.2).
 */
export interface PaletteContext {
  goto(href: string): Promise<void>;
  seats: SeatsStoreLike;
  status: StatusStoreLike;
  selection: SelectionLike;
  /** the open seat, or null when none is open */
  session(): SeatSessionLike | null;
  /** open one model in the inspector */
  inspect(id: string, modality: Modality): void;
}
