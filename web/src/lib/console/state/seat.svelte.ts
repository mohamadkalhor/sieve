/**
 * One seat: what it is, what it would ship, and every edit it accepts
 * (CONSOLE.md sections 5.2 and 5.3).
 *
 * This is the old profile page's script, moved out of the component so the
 * pane, the inspector and the keyboard all edit the same value. Three things in
 * it are load-bearing and are not to be "simplified".
 *
 * **One generation counter.** Opening another seat, or leaving, bumps it, and
 * every answer carries the generation it was asked under. A slow answer for the
 * seat you just left must never overwrite the seat you are looking at.
 *
 * **A revision per edit.** Every edit that changes what would be sent bumps it
 * *before* the debounce, and the write and the preview each remember the
 * revision they answered. That is what stops a ship from applying a lineup
 * computed for weights the person has already changed, and what stops an old
 * preview from patching the seats list with numbers nobody can see any more.
 *
 * **One write queue.** Settings go out through a promise chain, so an older PUT
 * can never finish last on the server, and a ship can drain the queue before it
 * applies.
 *
 * No `$effect` lives here: the route owns the single effect that opens a seat,
 * and the class works outside a component, which is how it is tested.
 */
import { api as defaultApi, explainError } from '$lib/api/client';
import type {
  ApiError,
  ApplyOutcome,
  AxisRow,
  HistoryRow,
  PreviewResult,
  ProfileMode,
  ProfileSettings,
  RequestOptions,
  SeatRow,
  SettingsPatch
} from '$lib/api/client';
import type { Chain, Profile } from '$lib/types';
import { session } from '$lib/session.svelte';
import {
  DEBOUNCE_MS,
  listState,
  shipState,
  same,
  type ListState,
  type ShipState
} from '$lib/profile/tune';
import type { SeatSessionLike } from '../contracts';
import { diffLineup, shipLabel, type LineupDiff } from '../logic/diff';
import { fromServer, setMode as setModeOn, toPatch, type Settings } from '../logic/settings';

/** The slice of the client one seat calls, so a test can answer it by hand. */
export interface SeatApi {
  profile(name: string, o?: RequestOptions): Promise<{ ok: true; value: Profile } | { ok: false; error: ApiError }>;
  profileSettings(
    name: string,
    o?: RequestOptions
  ): Promise<{ ok: true; value: ProfileSettings } | { ok: false; error: ApiError }>;
  chain(name: string, o?: RequestOptions): Promise<{ ok: true; value: Chain } | { ok: false; error: ApiError }>;
  axes(modality: string | undefined, o?: RequestOptions): Promise<{ ok: true; value: AxisRow[] } | { ok: false; error: ApiError }>;
  costMultipliers(o?: RequestOptions): Promise<{ ok: true; value: Record<string, number> } | { ok: false; error: ApiError }>;
  preview(
    name: string,
    body: SettingsPatch,
    o?: RequestOptions
  ): Promise<{ ok: true; value: PreviewResult } | { ok: false; error: ApiError }>;
  saveProfileSettings(
    name: string,
    body: SettingsPatch,
    o?: RequestOptions
  ): Promise<{ ok: true; value: ProfileSettings } | { ok: false; error: ApiError }>;
  applyProfile(
    name: string,
    o?: RequestOptions
  ): Promise<{ ok: true; value: ApplyOutcome } | { ok: false; error: ApiError }>;
  linkUnscored(
    body: { local_id: string; modality: string; name?: string },
    o?: RequestOptions
  ): Promise<{ ok: true; value: { model_id: string; local_id: string } } | { ok: false; error: ApiError }>;
  history(name: string, o?: RequestOptions): Promise<{ ok: true; value: HistoryRow[] } | { ok: false; error: ApiError }>;
  newProfile(
    body: { name: string; modality: string; from?: string; copy_from?: string },
    o?: RequestOptions
  ): Promise<{ ok: true; value: Profile } | { ok: false; error: ApiError }>;
  renameProfile(
    name: string,
    next: string,
    o?: RequestOptions
  ): Promise<{ ok: true; value: Profile } | { ok: false; error: ApiError }>;
  removeProfile(
    name: string,
    force?: boolean,
    o?: RequestOptions
  ): Promise<{ ok: true; value: unknown } | { ok: false; error: ApiError }>;
  setPurpose(
    name: string,
    purpose: string,
    o?: RequestOptions
  ): Promise<{ ok: true; value: Profile } | { ok: false; error: ApiError }>;
}

/**
 * What a seat borrows from its surroundings.
 *
 * Every one of these is injected rather than reached for, so the class has no
 * `window`, no clock and no global client inside it: a test drives the debounce,
 * the busy ticker and the token without a browser, and the route hands the real
 * ones in.
 */
export interface SeatSessionDeps {
  api: SeatApi;
  /** `localStorage`, or null where there is none: locks are a convenience */
  storage: Storage | null;
  setTimeout(fn: () => void, ms: number): number;
  clearTimeout(id: number): void;
  now(): number;
  /** the bearer token, if this browser has one */
  token(): string | undefined;
  /** after a successful write: the seats list follows the seat, no refetch */
  patch?(name: string, part: Partial<SeatRow>): void;
  /** after a successful write, for whatever else watches the seat */
  onSaved?(name: string): void;
  onShipped?(name: string, outcome: ApplyOutcome): void;
}

/** How often `waitedMs` is refreshed while an answer is in flight. */
const TICK_MS = 500;

function copySettings(settings: Settings): Settings {
  return {
    weights: { ...settings.weights },
    order: [...settings.order],
    locked: [...settings.locked],
    ship: settings.ship,
    mode: settings.mode,
    manual: [...settings.manual],
    pinned: [...settings.pinned],
    removed: [...settings.removed],
    needs: [...settings.needs],
    prefixWeights: { ...settings.prefixWeights }
  };
}

/** Whether two patches would send the same thing, key order included. */
function samePatch(left: SettingsPatch, right: SettingsPatch): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export class SeatSession implements SeatSessionLike {
  readonly name: string;

  /** bumped on open and close: an answer under an old generation is dropped */
  private gen = 0;
  /** bumped by every edit that changes what would be sent */
  private revision = $state(0);
  /** the revision whose write came back accepted */
  private savedRevision = 0;
  /** the revision the visible preview answers, or -1 */
  private previewRevision = $state(-1);
  /** preview requests: the last one asked is the only one that may answer */
  private inflight = 0;
  private timer: number | null = null;
  private ticker: number | null = null;
  /** the settings write queue: one PUT at a time, in order */
  private writes: Promise<void> = Promise.resolve();
  private queued = -1;
  private closed = false;
  private deps: SeatSessionDeps;

  profile = $state<Profile | null>(null);
  chain = $state<Chain | null>(null);
  everyAxis = $state<AxisRow[]>([]);
  prefixes = $state<string[]>([]);
  settings = $state<Settings | null>(null);
  /** the weights the seat arrived with: what "Reset" puts back */
  loaded = $state<Record<string, number>>({});
  preview = $state<PreviewResult | null>(null);
  pending = $state(false);
  waitedMs = $state(0);
  failed = $state<string | null>(null);
  shipping = $state(false);
  linking = $state<string | null>(null);
  said = $state<{ ok: boolean; text: string } | null>(null);
  gone = $state<ApiError | null>(null);
  loading = $state(true);
  saving = $state(false);
  /** the settings as the seat opened: what "Restore opened settings" puts back */
  opened: Settings | null = null;
  /** the ask bar: which question is open, and what has been typed */
  ask = $state<{ what: '' | 'copy' | 'rename' | 'delete'; name: string }>({ what: '', name: '' });
  historyOpen = $state(false);
  historyRows = $state<HistoryRow[] | null>(null);
  historyError = $state<ApiError | null>(null);
  historyLoading = $state(false);

  /** the ids this seat ships right now, or null when no chain was read */
  live = $derived(this.chain ? [this.chain.primary, ...(this.chain.fallbacks ?? [])] : null);
  /** the ids these settings would ship, or null until a preview answers */
  lineup = $derived(this.preview ? this.preview.models.map((row) => row.id) : null);
  listing = $derived(
    listState({
      pending: this.pending,
      waitedMs: this.waitedMs,
      error: this.failed,
      models: this.preview?.models ?? null
    })
  );
  diff = $derived<LineupDiff>(diffLineup(this.live, this.lineup));
  button = $derived<ShipState>(
    shipState({
      shipped: this.live ?? [],
      next: this.lineup ?? [],
      shipping: this.shipping,
      // "Ready" means the list has answered *for this draft*. A lineup is an
      // answer and so is "there is none"; ranking, busy and error are not. An
      // answer that came back for weights the person has already moved on from
      // would otherwise let the button say "this is already what ships" about a
      // lineup nobody is looking at.
      ready:
        (this.listing === 'ready' || this.listing === 'empty') &&
        this.previewRevision === this.revision
    })
  );
  shipText = $derived(shipLabel(this.diff, this.button));
  /** axis name -> what it is called on screen */
  labels = $derived(
    Object.fromEntries(this.everyAxis.map((axis) => [axis.name, axis.label ?? axis.name]))
  );
  meanings = $derived(
    Object.fromEntries(this.everyAxis.map((axis) => [axis.name, axis.meaning ?? '']))
  );
  /** the axes this profile does not score by yet, for "Add an axis" */
  spare = $derived(
    this.everyAxis.filter((axis) => !(axis.name in (this.settings?.weights ?? {})))
  );

  constructor(name: string, deps: SeatSessionDeps) {
    this.name = name;
    this.deps = deps;
  }

  /* ---------------------------------------------------------------------- */
  /* reading                                                                 */
  /* ---------------------------------------------------------------------- */

  async open(): Promise<void> {
    const gen = ++this.gen;
    this.closed = false;
    this.loading = true;
    this.gone = null;
    this.preview = null;
    this.failed = null;
    this.pending = false;
    this.said = null;
    this.historyRows = null;
    this.historyError = null;
    this.historyOpen = false;
    this.ask = { what: '', name: '' };
    this.clearTimer();

    const wanted = this.name;
    const [p, s, c] = await Promise.all([
      this.deps.api.profile(wanted, this.options()),
      this.deps.api.profileSettings(wanted, this.options()),
      this.deps.api.chain(wanted, this.options())
    ]);
    if (gen !== this.gen) return;

    if (!p.ok) {
      this.gone = p.error;
      this.loading = false;
      return;
    }

    this.profile = p.value;
    this.chain = c.ok ? c.value : null;

    // A server without the settings route answers 404 there; the profile's own
    // weights and policy are the honest fallback.
    const held: ProfileSettings | null = s.ok && s.value?.weights ? s.value : null;
    const settings = fromServer(p.value, held, this.readLocks());
    this.settings = settings;
    this.loaded = { ...settings.weights };
    this.opened = copySettings(settings);
    this.revision = 0;
    this.savedRevision = 0;
    this.previewRevision = -1;
    this.queued = -1;
    this.writes = Promise.resolve();
    this.loading = false;

    const [axes, prices] = await Promise.all([
      this.deps.api.axes(p.value.modality, this.options()),
      this.deps.api.costMultipliers(this.options())
    ]);
    if (gen !== this.gen) return;
    if (axes.ok && Array.isArray(axes.value)) this.everyAxis = axes.value;
    if (prices.ok && prices.value) this.prefixes = Object.keys(prices.value).sort();

    await this.refresh();
  }

  close(): void {
    this.closed = true;
    this.gen += 1;
    this.clearTimer();
    this.stopTicker();
    this.pending = false;
    this.ask = { what: '', name: '' };
    this.historyOpen = false;
  }

  /* ---------------------------------------------------------------------- */
  /* editing                                                                 */
  /* ---------------------------------------------------------------------- */

  /**
   * Take the settings a control just produced.
   *
   * `dropAxis` hands back `{ error }` rather than settings when the last axis
   * would go, and that lands in `said` like any other refusal. Locks travel
   * with the settings but never in a patch, so an edit that only moves a lock
   * is written to this browser and nowhere else.
   */
  edit(next: Settings | { error: string }): void {
    if ('error' in next) {
      this.said = { ok: false, text: next.error };
      return;
    }
    const before = this.settings;
    if (before === null) return;
    if (next === before) return;

    this.settings = next;
    this.said = null;
    this.writeLocks();

    if (samePatch(toPatch(next, this.axisNames()), toPatch(before, this.axisNames()))) return;

    this.revision += 1;
    // The visible preview answered an older revision now, so it may not patch
    // the seats list until an answer for this one arrives.
    this.previewRevision = -1;
    this.schedule();
  }

  /** Auto or manual, starting a hand-made list from what ships now. */
  setMode(mode: ProfileMode): void {
    if (!this.settings) return;
    this.edit(setModeOn(this.settings, mode, this.lineup ?? []));
  }

  /** Put back everything as the seat opened, without touching what shipped. */
  restoreOpened(): void {
    if (!this.opened) return;
    this.edit(copySettings(this.opened));
  }

  /** The weights the seat arrived with, back. */
  resetWeights(): void {
    if (!this.settings) return;
    this.edit({ ...this.settings, weights: { ...this.loaded }, order: Object.keys(this.loaded) });
  }

  /** Save and preview now rather than after the debounce. */
  async settle(): Promise<void> {
    this.clearTimer();
    if (!this.settings) return;
    await this.saveFor(this.revision, copySettings(this.settings));
    await this.previewFor(this.revision, copySettings(this.settings));
  }

  /* ---------------------------------------------------------------------- */
  /* the list                                                                */
  /* ---------------------------------------------------------------------- */

  async refresh(): Promise<void> {
    if (!this.settings) return;
    await this.previewFor(this.revision, copySettings(this.settings));
  }

  private async previewFor(rev: number, snapshot: Settings): Promise<void> {
    const gen = this.gen;
    const mine = ++this.inflight;
    this.pending = true;
    this.failed = null;
    this.waitedMs = 0;
    const started = this.deps.now();
    this.startTicker(started);

    const result = await this.deps.api.preview(this.name, toPatch(snapshot, this.axisNames()), this.options());
    if (gen !== this.gen || mine !== this.inflight) return;

    this.stopTicker();
    this.pending = false;
    this.waitedMs = 0;
    if (!result.ok) {
      // The previous lineup stays on screen, dimmed: an answer that failed is
      // not an answer that the list is empty.
      this.failed = explainError(result.error);
      return;
    }

    const value = result.value;
    this.preview = {
      ...value,
      // A server that sends no pool has not said the pool is empty; keeping the
      // one we had is what lets the hand-picker keep working.
      pool: value.pool ?? this.preview?.pool,
      unlinked: value.unlinked ?? this.preview?.unlinked
    };
    this.previewRevision = rev;
    this.patchFromPreview();
  }

  /* ---------------------------------------------------------------------- */
  /* writing                                                                 */
  /* ---------------------------------------------------------------------- */

  private schedule(): void {
    this.clearTimer();
    this.timer = this.deps.setTimeout(() => {
      this.timer = null;
      const rev = this.revision;
      const snapshot = this.settings ? copySettings(this.settings) : null;
      if (!snapshot) return;
      void this.saveFor(rev, snapshot);
      void this.previewFor(rev, snapshot);
    }, DEBOUNCE_MS);
  }

  /**
   * Queue one write, and answer with whether it was accepted.
   *
   * The chain is what keeps the server in step with the screen: two edits 100 ms
   * apart must not arrive in the other order. A write whose revision has already
   * been superseded by a queued one is skipped, because the newer snapshot says
   * everything the older one said.
   */
  private saveFor(rev: number, snapshot: Settings): Promise<boolean> {
    this.queued = Math.max(this.queued, rev);
    const gen = this.gen;
    const run = this.writes.then(async () => {
      if (gen !== this.gen || this.closed) return false;
      if (rev < this.queued) return true; // a newer snapshot is already queued
      this.saving = true;
      const result = await this.deps.api.saveProfileSettings(
        this.name,
        toPatch(snapshot, this.axisNames()),
        this.options()
      );
      this.saving = false;
      if (gen !== this.gen) return false;
      if (!result.ok) {
        this.said = { ok: false, text: explainError(result.error) };
        return false;
      }
      this.savedRevision = Math.max(this.savedRevision, rev);
      this.deps.onSaved?.(this.name);
      this.patchFromPreview();
      return true;
    });
    this.writes = run.then(
      () => undefined,
      () => undefined
    );
    return run;
  }

  /* ---------------------------------------------------------------------- */
  /* shipping and the menu                                                   */
  /* ---------------------------------------------------------------------- */

  async ship(): Promise<void> {
    if (this.button.disabled || !this.settings) return;
    const gen = this.gen;
    this.shipping = true;
    this.said = null;
    this.clearTimer();

    const rev = this.revision;
    const snapshot = copySettings(this.settings);
    // What the server has is what apply ships, so the settings it has must be
    // these. When this draft is already written there is nothing to send.
    const saved = this.savedRevision >= rev ? true : await this.saveFor(rev, snapshot);
    if (gen !== this.gen) return;
    if (!saved) {
      this.shipping = false;
      return;
    }

    // Apply ships what the server has, so it may only go out when the preview on
    // screen was computed from exactly the settings that were just saved.
    if (this.previewRevision !== rev) {
      await this.previewFor(rev, snapshot);
      if (gen !== this.gen) return;
      if (this.previewRevision !== rev) {
        this.shipping = false;
        this.said = {
          ok: false,
          text: this.failed ?? 'Could not check what these settings would ship.'
        };
        return;
      }
    }

    const result = await this.deps.api.applyProfile(this.name, this.options());
    if (gen !== this.gen) return;
    this.shipping = false;
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return;
    }

    const value = result.value;
    this.chain = value?.chain ?? this.chain;
    const combo = (value?.combos ?? []).join(', ');
    this.said = { ok: true, text: combo ? `Shipped as ${combo}.` : 'Shipped.' };
    this.deps.onShipped?.(this.name, value);
    this.patchAfterShip(value);
    void this.refresh();
  }

  async linkThen(localId: string, then: (id: string) => Settings): Promise<void> {
    if (!this.profile || this.linking) return;
    const gen = this.gen;
    this.linking = localId;
    const result = await this.deps.api.linkUnscored(
      {
        local_id: localId,
        modality: this.profile.modality,
        name: localId.split('/').pop()
      },
      this.options()
    );
    if (gen !== this.gen) return;
    this.linking = null;
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return;
    }
    // It is linked: it is no longer one of the ids that matched nothing.
    if (this.preview?.unlinked) {
      this.preview = {
        ...this.preview,
        unlinked: this.preview.unlinked.filter((row) => row.local_id !== localId)
      };
    }
    this.edit(then(result.value.model_id));
  }

  /* ---------------------------------------------------------------------- */
  /* the seat's own life                                                     */
  /* ---------------------------------------------------------------------- */

  async copy(to: string): Promise<string | null> {
    const wanted = to.trim();
    if (!wanted || !this.profile) return null;
    const result = await this.deps.api.newProfile(
      { name: wanted, modality: this.profile.modality, from: this.name, copy_from: this.name },
      this.options()
    );
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return null;
    }
    this.ask = { what: '', name: '' };
    return wanted;
  }

  async rename(to: string): Promise<string | null> {
    const wanted = to.trim();
    if (!wanted || wanted === this.name) {
      this.ask = { what: '', name: '' };
      return null;
    }
    const result = await this.deps.api.renameProfile(this.name, wanted, this.options());
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return null;
    }
    this.ask = { what: '', name: '' };
    return wanted;
  }

  /** Delete asks once, and retries with `force` when the seat is in use. */
  async destroy(): Promise<boolean> {
    let result = await this.deps.api.removeProfile(this.name, false, this.options());
    if (!result.ok && result.error.code === 'in_use') {
      result = await this.deps.api.removeProfile(this.name, true, this.options());
    }
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return false;
    }
    this.ask = { what: '', name: '' };
    return true;
  }

  /** Who changed what, from what to what. */
  async history(): Promise<HistoryRow[]> {
    const gen = this.gen;
    this.historyLoading = true;
    this.historyError = null;
    const result = await this.deps.api.history(this.name, this.options());
    if (gen !== this.gen) return [];
    this.historyLoading = false;
    if (!result.ok || !Array.isArray(result.value)) {
      // A failure is not an empty history: say so, and keep the last answer.
      this.historyError = result.ok
        ? { code: 'bad_answer', message: 'The history came back unreadable.', status: 0 }
        : result.error;
      return this.historyRows ?? [];
    }
    this.historyRows = result.value;
    return result.value;
  }

  async openHistory(): Promise<void> {
    this.ask = { what: '', name: '' };
    if (this.historyOpen) {
      this.historyOpen = false;
      return;
    }
    this.historyOpen = true;
    await this.history();
  }

  closeHistory(): void {
    this.historyOpen = false;
  }

  /** The purpose, saved on its own: it is not part of the settings patch. */
  async setPurpose(text: string): Promise<void> {
    const wanted = text.trim();
    if (!this.profile || wanted === this.profile.purpose) return;
    const gen = this.gen;
    const result = await this.deps.api.setPurpose(this.name, wanted, this.options());
    if (gen !== this.gen) return;
    if (!result.ok) {
      this.said = { ok: false, text: explainError(result.error) };
      return;
    }
    this.profile = result.value;
    this.said = { ok: true, text: 'Saved.' };
    this.deps.onSaved?.(this.name);
    this.deps.patch?.(this.name, { purpose: wanted });
  }

  askAbout(what: '' | 'copy' | 'rename' | 'delete'): void {
    this.ask = { what, name: what === 'copy' ? `${this.name}_copy` : this.name };
  }

  /* ---------------------------------------------------------------------- */
  /* plumbing                                                                */
  /* ---------------------------------------------------------------------- */

  private axisNames(): string[] {
    return this.everyAxis.map((axis) => axis.name);
  }

  private options(): RequestOptions {
    const token = this.deps.token();
    return token ? { token } : {};
  }

  private lockKey(): string {
    return `sieve:locks:${this.name}`;
  }

  private readLocks(): string[] {
    try {
      const stored = JSON.parse(this.deps.storage?.getItem(this.lockKey()) ?? '[]');
      return Array.isArray(stored) ? stored.filter((axis) => typeof axis === 'string') : [];
    } catch {
      return [];
    }
  }

  private writeLocks(): void {
    try {
      this.deps.storage?.setItem(this.lockKey(), JSON.stringify(this.settings?.locked ?? []));
    } catch {
      /* a remembered lock is a convenience, not state */
    }
  }

  private startTicker(started: number): void {
    if (this.ticker !== null) return;
    this.ticker = this.deps.setTimeout(() => {
      this.ticker = null;
      if (!this.pending) return;
      this.waitedMs = this.deps.now() - started;
      this.startTicker(started);
    }, TICK_MS) as unknown as number;
  }

  private stopTicker(): void {
    if (this.ticker === null) return;
    this.deps.clearTimeout(this.ticker);
    this.ticker = null;
  }

  private clearTimer(): void {
    if (this.timer === null) return;
    this.deps.clearTimeout(this.timer);
    this.timer = null;
  }

  /**
   * Follow the seat in the list, but only from an answer that is still true.
   *
   * The list shows what the *saved* settings would ship, so a preview that
   * answered an unsaved edit must not write to it: the number would be right for
   * a draft and wrong for the list.
   */
  private patchFromPreview(): void {
    const patch = this.deps.patch;
    if (!patch || !this.preview || !this.settings) return;
    if (this.previewRevision !== this.savedRevision) return;
    const lineup = this.preview.models.map((row) => ({ id: row.id, name: row.name }));
    const live = this.live;
    patch(this.name, {
      lineup,
      in_step: live ? same(live, lineup.map((row) => row.id)) : null,
      changes: this.diff.known ? this.diff.changes : null,
      mode: this.settings.mode,
      ship: this.settings.ship,
      purpose: this.profile?.purpose
    });
  }

  private patchAfterShip(outcome: ApplyOutcome | null): void {
    const patch = this.deps.patch;
    if (!patch) return;
    const chain = outcome?.chain ?? this.chain;
    const live = chain ? [chain.primary, ...(chain.fallbacks ?? [])] : null;
    patch(this.name, {
      live: live ? live.map((id) => ({ id, name: this.nameOf(id) })) : null,
      in_step: live && this.lineup ? same(live, this.lineup) : null,
      changes: live && this.lineup ? this.diff.changes : null,
      shipped_at: outcome?.shipped_at ?? null
    });
  }

  private nameOf(id: string): string {
    const rows = [...(this.preview?.models ?? []), ...(this.preview?.next ?? [])];
    return rows.find((row) => row.id === id)?.name ?? id;
  }
}

/**
 * The real dependencies, for the route: the client, this browser's storage and
 * clock, the session's token, and the seats list to follow.
 */
export function browserDeps(
  extra: Pick<SeatSessionDeps, 'patch' | 'onSaved' | 'onShipped'> = {}
): SeatSessionDeps {
  return {
    api: defaultApi as unknown as SeatApi,
    storage: typeof localStorage === 'undefined' ? null : localStorage,
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: (id) => window.clearTimeout(id),
    now: () => Date.now(),
    token: () => session.token || undefined,
    ...extra
  };
}
