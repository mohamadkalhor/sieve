<script lang="ts">
  /**
   * One profile, whole.
   *
   * The list gives a seat a row: its card, the handful of controls somebody
   * moves every day, and the list those controls would ship. Everything else
   * used to unfold *inside* that row, which made the list as long as the
   * deepest thing open on it and buried the history and the experience at the
   * bottom of a page about twenty-two other seats.
   *
   * Here the seat has its own page. Three columns, because the three questions
   * are asked together: what is this seat (left), what does it care about
   * (centre), and what would it ship (right). Under them, tabs rather than
   * more scroll -- the ranking detail alone is most of a megabyte on this box,
   * so it is fetched when its tab is opened and not before.
   *
   * WHAT IT WRITES, AND WITH WHICH ROUTE
   *
   *   description  PUT /v1/profiles/{name}  -- the whole profile back with
   *                `purpose` changed, because that is the only route that
   *                writes it. The profile is re-read first, so the round trip
   *                cannot carry a stale copy of everything else.
   *   weights,     PUT /v1/profiles/{name}/settings, and where that route is
   *   list,        absent the two phase-1 routes (weights, policy) between
   *   floor …      them hold everything this page can change.
   *   pin/remove   PUT …/models/{id}/status, written through on click.
   *
   * REMOVING AN AXIS
   *
   * The settings route merges the weights it is given into the ones it holds,
   * so no request can drop a key. Removing an axis therefore sends it as zero
   * and hides it, which is the same arithmetic -- a zero-weighted axis
   * contributes nothing to the score and nothing to the confidence -- and it
   * survives a reload, because an axis at zero is drawn as not chosen.
   */
  import { goto } from '$app/navigation';
  import {
    api,
    explainError,
    type ApiError,
    type ExperienceRow,
    type HistoryRow,
    type ModelStatus,
    type PreviewResult,
    type ProfileSettings,
    type Result,
    type WeightControl
  } from '$lib/api/client';
  import type { Axis, Chain, Modality, Profile, Ranking } from '$lib/types';
  import AxisBars from '$lib/components/AxisBars.svelte';
  import ConfDots from '$lib/components/ConfDots.svelte';
  import Empty from '$lib/components/Empty.svelte';
  import { runPulse } from '$lib/refresh.svelte';
  import { session } from '$lib/session.svelte';
  // aliased: `ProfileSettings` is already the name of the settings *shape*
  import SettingsBlocks from '$lib/components/ProfileSettings.svelte';
  import WeightSlider from '$lib/components/WeightSlider.svelte';
  import { ago } from '$lib/freshness';
  import { duration, reducedMotion } from '$lib/motion/reduced';
  import { rankWithFloor, renormalise, weigh, type AxesByModel } from '$lib/rank/weigh';
  import { flip } from 'svelte/animate';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  const name = $derived(data.name);

  let profile = $state<Profile | null>(null);
  let settings = $state<ProfileSettings | null>(null);
  let draft = $state<ProfileSettings | null>(null);
  let chain = $state<Chain | null>(null);
  let ranking = $state<Ranking | null>(null);
  let preview = $state<PreviewResult | null>(null);
  let everyAxis = $state<Axis[]>([]);
  let defaults = $state<Record<string, number> | null>(null);

  let loading = $state(true);
  let gone = $state<ApiError | null>(null);
  /** one token for the whole app, and none at all when gate signed you in */
  const token = $derived(session.token);
  let said = $state<{ ok: boolean; text: string } | null>(null);
  let busy = $state('');

  /** the settings route is not on this server yet */
  let settingsAbsent = $state(false);
  /** the preview route is not on this server yet: the local rank stands */
  let previewAbsent = $state(false);
  /** the status route is not on this server yet: pin and remove are off */
  let statusAbsent = $state(false);

  /** this page has asked the server for its list at least once */
  let engaged = $state(false);
  let previewing = $state(false);
  let statuses = $state<Record<string, ModelStatus>>({});

  /** the axes this seat counts, in the order they are drawn */
  let chosen = $state<string[]>([]);

  /** ticks, so "shipped 51 min ago" keeps counting */
  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  const options = $derived({ token: token || undefined });

  /**
   * A route that is not mounted does not always answer 404. FastAPI serves the
   * built web app at `/`, so a path it does not recognise comes back as the SPA
   * shell -- 200, text/html, which the client hands over as a null body.
   */
  function absent<T>(result: Result<T>): boolean {
    if (!result.ok) return result.error.status === 404 || result.error.code === 'not_built';
    return result.value === null || result.value === undefined;
  }

  const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

  /** The controls a profile implies, for a server without the settings route. */
  function fromProfile(p: Profile): ProfileSettings {
    const weights: Record<string, WeightControl> = {};
    for (const [axis, value] of Object.entries(p.weights)) {
      weights[axis] = { value, min: 0, max: 1, locked: false };
    }
    return {
      list_length: p.policy?.chain ?? 5,
      floor_score: p.policy?.min_confidence ?? 0,
      price_sensitivity: 1,
      experience_weight: 0,
      auto_apply: p.policy?.auto_apply ?? false,
      weights,
      cost_multipliers: {}
    };
  }

  /* ---------------------------------------------------------------------- */
  /* loading: the light half                                                 */
  /* ---------------------------------------------------------------------- */

  let description = $state('');

  // Plain variables, not state: they guard the tab fetches below and must not
  // re-run the effect that sets them.
  let askedRanking = false;
  let askedHistory = false;
  let askedExperience = false;

  $effect(() => {
    // a finished run re-ranks this seat; the preview and the list must follow
    runPulse.seen();
    const wanted = name;
    loading = true;
    engaged = false;
    preview = null;
    ranking = null;
    askedRanking = false;
    askedHistory = false;
    askedExperience = false;
    void (async () => {
      const [p, s, c, m] = await Promise.all([
        api.profile(wanted),
        api.profileSettings(wanted),
        api.chain(wanted),
        api.costMultipliers()
      ]);
      if (wanted !== name) return;

      if (!p.ok || !p.value) {
        gone = p.ok
          ? { code: 'not_found', message: `There is no profile called ${wanted}.`, status: 404 }
          : p.error;
        loading = false;
        return;
      }
      gone = null;
      profile = p.value;
      description = p.value.purpose ?? '';
      chain = c.ok ? c.value : null;
      defaults = m.ok && m.value && typeof m.value === 'object' ? m.value : null;

      if (absent(s) || !s.ok || !s.value.weights) {
        settings = fromProfile(p.value);
        settingsAbsent = true;
      } else {
        settings = s.value;
        settingsAbsent = false;
      }
      draft = clone(settings);
      // An axis at zero counts for nothing, so it is drawn as not chosen --
      // which is what makes "remove" survive a reload on a server whose
      // settings route cannot drop a key.
      chosen = Object.entries(settings.weights)
        .filter(([, control]) => control.value > 0)
        .sort(([aAxis, a], [bAxis, b]) => b.value - a.value || aAxis.localeCompare(bAxis))
        .map(([axis]) => axis);
      loading = false;

      const found = await api.axes(p.value.modality as Modality);
      if (wanted !== name) return;
      everyAxis = found.ok && Array.isArray(found.value) ? found.value : [];
    })();
  });

  /* ---------------------------------------------------------------------- */
  /* the list on the right                                                   */
  /* ---------------------------------------------------------------------- */

  interface Row {
    rank: number;
    id: string;
    local_ids: string[];
    score: number;
    status: ModelStatus;
  }

  const axesByModel = $derived.by(() => {
    const out: AxesByModel = {};
    for (const rank of ranking?.ranks ?? []) {
      if (rank.excluded_by === 'min_confidence' || rank.dominated_by) continue;
      out[rank.model_id] = Object.fromEntries(
        (rank.axes ?? []).map((axis) => [axis.axis, { value: axis.value, coverage: axis.coverage }])
      );
    }
    return out;
  });

  const localIds = $derived.by(() => {
    const out: Record<string, string[]> = {};
    for (const rank of ranking?.ranks ?? []) out[rank.model_id] = rank.local_ids ?? [];
    return out;
  });

  const weightValues = $derived.by(() => {
    const out: Record<string, number> = {};
    for (const [axis, control] of Object.entries(draft?.weights ?? {})) out[axis] = control.value;
    return out;
  });

  function arrange(rows: Row[]): Row[] {
    const kept = rows.filter((row) => row.status !== 'removed');
    const pinned = kept.filter((row) => row.status === 'pinned');
    const rest = kept.filter((row) => row.status !== 'pinned');
    return [...pinned, ...rest].map((row, index) => ({ ...row, rank: index + 1 }));
  }

  const localRows = $derived.by(() =>
    rankWithFloor(weigh(axesByModel, weightValues), draft?.floor_score ?? 0).map((row, index) => ({
      rank: index + 1,
      id: row.model_id,
      local_ids: localIds[row.model_id] ?? [],
      score: row.score,
      status: statuses[row.model_id] ?? ('active' as ModelStatus)
    }))
  );

  const previewRows = $derived.by(() => {
    if (!preview) return null;
    const scored = new Map((preview.ranking?.ranks ?? []).map((rank) => [rank.model_id, rank]));
    return preview.models.map((id, index) => ({
      rank: index + 1,
      id,
      local_ids: scored.get(id)?.local_ids ?? localIds[id] ?? [],
      score: scored.get(id)?.final ?? 0,
      status: statuses[id] ?? ('active' as ModelStatus)
    }));
  });

  const shippedIds = $derived.by(() => {
    if (!chain) return [] as string[];
    return [chain.primary, ...(chain.fallbacks ?? [])].filter((id): id is string => !!id);
  });
  const shippedAt = $derived.by(() => {
    const at = new Map<string, number>();
    shippedIds.forEach((id, index) => at.set(id, index + 1));
    return at;
  });

  const cut = $derived(Math.max(1, draft?.list_length ?? 5));

  const shippedRows = $derived(
    shippedIds.map((id, index) => ({
      rank: index + 1,
      id,
      local_ids: chain?.local?.[id] ?? [],
      score: 0,
      status: 'active' as ModelStatus
    }))
  );

  const shipping = $derived(
    previewRows ?? (engaged ? arrange(localRows).slice(0, cut) : shippedRows)
  );

  const dropping = $derived(
    shippedIds
      .filter((id) => !shipping.some((row) => row.id === id))
      .map((id, index) => ({ id, was: shippedAt.get(id) ?? index + 1 }))
  );

  const dirty = $derived(
    draft !== null && settings !== null && JSON.stringify(draft) !== JSON.stringify(settings)
  );

  const chip = $derived.by(() => {
    if (loading) return { text: 'loading…', tone: 'muted' };
    if (dirty) return { text: 'changed, not applied', tone: 'accent' };
    if (!chain) return { text: 'no chain yet', tone: 'muted' };
    const when = chain.computed_at ? ago(new Date(chain.computed_at), now) : 'at some point';
    return { text: `shipped ${when}${draft?.auto_apply ? ' · auto' : ''}`, tone: 'good' };
  });

  /* ---------------------------------------------------------------------- */
  /* the weights in the middle                                               */
  /* ---------------------------------------------------------------------- */

  const rows = $derived(
    chosen
      .map((axis) => [axis, draft?.weights[axis]] as const)
      .filter((pair): pair is readonly [string, WeightControl] => pair[1] !== undefined)
  );

  const sum = $derived(Object.values(weightValues).reduce((total, value) => total + value, 0));
  const balanced = $derived(Math.abs(sum - 1) <= 0.001);

  const described = $derived.by(() => {
    const out: Record<string, Axis> = {};
    for (const axis of everyAxis) out[axis.name] = axis;
    return out;
  });

  /** every axis of this modality this seat is not counting yet */
  const available = $derived(everyAxis.filter((axis) => !chosen.includes(axis.name)));

  let picking = $state(false);

  /**
   * Move one weight, and let the others absorb it.
   *
   * Locked axes hold. So do the ones that are not on this seat: an axis at
   * zero is removed, and a renormalise that quietly gave it a share of the
   * remainder would put it back without anybody asking for it.
   */
  function move(axis: string, value: number) {
    if (!draft) return;
    const held = new Set(
      Object.entries(draft.weights)
        .filter(([axisName, control]) => control.locked || !chosen.includes(axisName))
        .map(([axisName]) => axisName)
    );
    const next = renormalise(weightValues, axis, value, held);
    for (const [axisName, weight] of Object.entries(next)) {
      const control = draft.weights[axisName];
      if (control) control.value = weight;
    }
    touched();
  }

  function bound(axis: string, which: 'min' | 'max', value: number) {
    const control = draft?.weights[axis];
    if (!control || !Number.isFinite(value)) return;
    control[which] = Math.min(1, Math.max(0, value));
    if (control.min > control.max) control[which === 'min' ? 'max' : 'min'] = control[which];
    control.value = Math.min(control.max, Math.max(control.min, control.value));
    touched();
  }

  function addAxis(axis: string) {
    if (!draft) return;
    const existing = draft.weights[axis];
    if (existing) existing.value = Math.min(existing.max, Math.max(existing.min, 0.1));
    else draft.weights[axis] = { value: 0.1, min: 0, max: 1, locked: false };
    chosen = [...chosen, axis];
    picking = false;
    touched();
  }

  /**
   * Remove: zero and hidden.
   *
   * The settings route merges, so nothing can drop the key. Zero is the same
   * answer -- the axis carries no score and no confidence -- and it is the one
   * the server will still be holding after a reload.
   */
  function removeAxis(axis: string) {
    const control = draft?.weights[axis];
    if (control) control.value = 0;
    chosen = chosen.filter((held) => held !== axis);
    touched();
  }

  /** Scale the unlocked weights so the whole thing sums to 1. */
  function normalise() {
    if (!draft) return;
    const held = draft;
    const free = chosen.filter((axis) => !held.weights[axis]?.locked);
    const lockedTotal = chosen
      .filter((axis) => held.weights[axis]?.locked)
      .reduce((total, axis) => total + (held.weights[axis]?.value ?? 0), 0);
    const room = 1 - lockedTotal;
    if (free.length === 0 || room < 0) {
      said = {
        ok: false,
        text: 'Every axis here is locked, or the locked ones already add up to more than 1.'
      };
      return;
    }
    const freeTotal = free.reduce((total, axis) => total + (held.weights[axis]?.value ?? 0), 0);
    for (const axis of free) {
      const control = held.weights[axis];
      if (!control) continue;
      const share = freeTotal > 0 ? (control.value / freeTotal) * room : room / free.length;
      control.value = Math.min(control.max, Math.max(control.min, share));
    }
    touched();
  }

  /* ---------------------------------------------------------------------- */
  /* preview                                                                 */
  /* ---------------------------------------------------------------------- */

  let timer: ReturnType<typeof setTimeout> | null = null;
  let latest = 0;

  async function engage() {
    if (engaged || !draft) return;
    engaged = true;
    await runPreview();
    if (previewAbsent && !ranking) await loadRanking();
  }

  function touched() {
    said = null;
    if (!engaged) void engage();
    else schedulePreview();
  }

  function schedulePreview() {
    if (previewAbsent || !draft) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void runPreview(), 250);
  }

  async function runPreview() {
    if (!draft || previewAbsent) return;
    const mine = ++latest;
    previewing = true;
    const result = await api.preview(name, clone(draft), options);
    if (mine !== latest) return;
    previewing = false;

    if (absent(result)) {
      previewAbsent = true;
      preview = null;
      return;
    }
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    preview = result.value;
    if (result.value.ranking) ranking = result.value.ranking;
  }

  $effect(() => () => {
    if (timer) clearTimeout(timer);
  });

  /* ---------------------------------------------------------------------- */
  /* writing                                                                 */
  /* ---------------------------------------------------------------------- */

  async function setStatus(id: string, status: ModelStatus) {
    const before = statuses[id] ?? 'active';
    statuses = { ...statuses, [id]: status };
    busy = `status:${id}`;
    const result = await api.setModelStatus(name, id, status, options);
    busy = '';

    if (absent(result)) {
      statuses = { ...statuses, [id]: before };
      statusAbsent = true;
      said = {
        ok: false,
        text: 'Pinning and removing need the per-model status route, which this server does not have yet.'
      };
      return;
    }
    if (!result.ok) {
      statuses = { ...statuses, [id]: before };
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    preview = null;
    schedulePreview();
  }

  async function save(): Promise<ApiError | null> {
    if (!draft) return null;
    if (!settingsAbsent) {
      const result = await api.saveProfileSettings(name, clone(draft), options);
      if (!absent(result)) {
        if (!result.ok) return result.error;
        settings = result.value;
        draft = clone(result.value);
        return null;
      }
      settingsAbsent = true;
    }

    const weights = await api.setWeights(name, clone(weightValues), options);
    if (!weights.ok) return weights.error;
    const policy = await api.setPolicy(
      name,
      {
        chain: draft.list_length,
        min_confidence: draft.floor_score,
        auto_apply: draft.auto_apply
      },
      options
    );
    if (!policy.ok) return policy.error;
    settings = clone(draft);
    return null;
  }

  async function apply() {
    busy = 'apply';
    said = null;

    const failed = await save();
    if (failed) {
      busy = '';
      said = { ok: false, text: explainError(failed) };
      return;
    }

    let where: string[] = [];
    const result = await api.applyProfile(name, options);
    if (absent(result)) {
      const legacy = await api.apply([name], options);
      busy = '';
      if (!legacy.ok) {
        said = { ok: false, text: explainError(legacy.error) };
        return;
      }
      where = (legacy.value ?? []).map((target) => target.target);
    } else {
      busy = '';
      if (!result.ok) {
        said = { ok: false, text: explainError(result.error) };
        return;
      }
      where = (result.value.results ?? []).map((target) => target.target);
      if (result.value.chain) chain = result.value.chain;
    }

    said = { ok: true, text: where.length ? `Shipped to ${where.join(', ')}.` : 'Shipped.' };
    const again = await api.chain(name);
    if (again.ok) chain = again.value;
  }

  function discard() {
    if (!settings) return;
    draft = clone(settings);
    chosen = Object.entries(settings.weights)
      .filter(([, control]) => control.value > 0)
      .sort(([aAxis, a], [bAxis, b]) => b.value - a.value || aAxis.localeCompare(bAxis))
      .map(([axis]) => axis);
    preview = null;
    said = null;
    schedulePreview();
  }

  async function toggleAuto(on: boolean) {
    if (!draft) return;
    draft.auto_apply = on;
    busy = 'auto';
    const failed = await save();
    busy = '';
    if (failed) said = { ok: false, text: explainError(failed) };
  }

  /* ---------------------------------------------------------------------- */
  /* the description, which only one route writes                            */
  /* ---------------------------------------------------------------------- */

  const describedDirty = $derived(profile !== null && description.trim() !== profile.purpose);

  async function saveDescription() {
    if (!profile) return;
    busy = 'describe';
    said = null;
    // Re-read first: `PUT /v1/profiles/{name}` takes the whole profile, and the
    // copy this page loaded may be minutes old.
    const fresh = await api.profile(name);
    const base = fresh.ok && fresh.value ? fresh.value : profile;
    const result = await api.saveProfile(
      name,
      { ...base, purpose: description.trim() },
      options
    );
    busy = '';
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    profile = result.value && result.value.name ? result.value : { ...base, purpose: description.trim() };
    said = { ok: true, text: 'Description saved.' };
  }

  /* ---------------------------------------------------------------------- */
  /* rename, copy, delete                                                    */
  /* ---------------------------------------------------------------------- */

  let renaming = $state('');
  $effect(() => {
    renaming = name;
  });
  let confirming = $state(false);
  let forcing = $state(false);
  let copying = $state('');

  async function rename() {
    const next = renaming.trim();
    if (!next || next === name) return;
    busy = 'rename';
    const result = await api.renameProfile(name, next, options);
    busy = '';
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    await goto(`/profiles/${encodeURIComponent(next)}`);
  }

  async function copy() {
    const next = copying.trim();
    if (!next || !profile) return;
    busy = 'copy';
    const result = await api.newProfile(
      { name: next, modality: profile.modality, from: name, copy_from: name },
      options
    );
    busy = '';
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    await goto(`/profiles/${encodeURIComponent(next)}`);
  }

  async function remove(force = false) {
    busy = 'delete';
    const result = await api.removeProfile(name, force, options);
    busy = '';
    confirming = false;
    if (!result.ok) {
      if (result.error.code === 'in_use') {
        forcing = true;
        said = { ok: false, text: `${result.error.message} — delete anyway?` };
        return;
      }
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    forcing = false;
    await goto('/profiles');
  }

  /* ---------------------------------------------------------------------- */
  /* the tabs, each loaded when it is opened                                 */
  /* ---------------------------------------------------------------------- */

  type Tab = 'ranking' | 'history' | 'experience' | 'costs';
  const TABS: { id: Tab; label: string }[] = [
    { id: 'ranking', label: 'Ranking detail' },
    { id: 'history', label: 'History' },
    { id: 'experience', label: 'Experience' },
    { id: 'costs', label: 'Cost multipliers' }
  ];

  let tab = $state<Tab | null>(null);

  let history = $state<HistoryRow[]>([]);
  let historyNote = $state('');
  let experience = $state<ExperienceRow[]>([]);
  let experienceNote = $state('');
  let rankingNote = $state('');

  async function loadRanking() {
    rankingNote = '';
    const result = await api.ranking(name);
    if (result.ok && result.value) ranking = result.value;
    else rankingNote = result.ok ? 'nothing ranked yet' : explainError(result.error);
  }

  async function loadHistory() {
    const wanted = name;
    const result = await api.history(wanted);
    if (wanted !== name) return;
    if (result.ok && Array.isArray(result.value)) {
      history = result.value;
      historyNote = result.value.length ? '' : 'nothing recorded yet';
      return;
    }
    // `/v1/decisions?profile=` has recorded every weight change, hold and apply
    // since phase 1, in all but the field names.
    const decisions = await api.decisions(wanted);
    if (wanted !== name) return;
    if (decisions.ok && Array.isArray(decisions.value)) {
      history = decisions.value.map((decision) => ({
        who: decision.actor,
        when: decision.at,
        what: `${decision.kind}: ${decision.reason}`,
        before: decision.before,
        after: decision.after
      }));
      historyNote = 'read from the decision log, until this profile has a history route';
    } else {
      history = [];
      historyNote = 'nothing recorded yet';
    }
  }

  async function loadExperience() {
    const wanted = name;
    const result = await api.experience(wanted);
    if (wanted !== name) return;
    if (result.ok && Array.isArray(result.value)) {
      experience = result.value;
      experienceNote = result.value.length ? '' : 'nothing called on this seat yet';
      return;
    }
    experience = [];
    experienceNote = 'this server has no experience route yet';
  }

  $effect(() => {
    if (loading) return;
    if (tab === 'ranking' && !askedRanking) {
      askedRanking = true;
      if (!ranking) void loadRanking();
    }
    if (tab === 'history' && !askedHistory) {
      askedHistory = true;
      void loadHistory();
    }
    if (tab === 'experience' && !askedExperience) {
      askedExperience = true;
      void loadExperience();
    }
  });

  /* ---- the ranking table ------------------------------------------------ */

  const TABLE = 50;
  const ASIDE = 25;

  const allRanked = $derived((ranking?.ranks ?? []).filter((rank) => rank.position > 0));
  const allAside = $derived((ranking?.ranks ?? []).filter((rank) => rank.position === 0));
  const ranked = $derived(allRanked.slice(0, TABLE));
  const setAside = $derived(allAside.slice(0, ASIDE));

  /* ---- the per-profile cost overrides ----------------------------------- */

  const prefixes = $derived(
    [...new Set([...Object.keys(defaults ?? {}), ...Object.keys(draft?.cost_multipliers ?? {})])].sort()
  );

  function override(prefix: string, raw: string) {
    if (!draft) return;
    const next = { ...draft.cost_multipliers };
    if (raw.trim() === '') delete next[prefix];
    else {
      const value = Number(raw);
      if (!Number.isFinite(value)) return;
      next[prefix] = value;
    }
    draft.cost_multipliers = next;
    touched();
  }

  const when = (value: string) =>
    value
      ? new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
      : '';

  /** `before`/`after` are whatever the server recorded; show them, don't parse them. */
  function show(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
</script>

<svelte:head><title>{name} · Profiles · Sieve</title></svelte:head>

<p class="back"><a href="/profiles">← All profiles</a></p>

{#if gone}
  <Empty error={gone} title={name} hint="Nothing here answers to that name." />
{:else}
  {#if !session.signedIn}
    <label class="token">
      <span>Token (needed to change anything)</span>
      <input
        type="password"
        bind:value={session.token}
        placeholder="a token with profiles:write and apply"
        autocomplete="off"
      />
    </label>
  {/if}

  <div class="three">
    <!-- LEFT: what this seat is ------------------------------------------ -->
    <aside class="side">
      <h1>{name}</h1>
      <p class="modality">{profile?.modality ?? ''}</p>
      <span class="chip" data-tone={chip.tone}>{chip.text}</span>

      <section class="block">
        <h2>Description</h2>
        <textarea
          rows="3"
          bind:value={description}
          placeholder="what this seat is for, in a sentence"
        ></textarea>
        <div class="acts">
          <button
            type="button"
            onclick={() => void saveDescription()}
            disabled={busy === 'describe' || !describedDirty || !description.trim()}
          >
            {busy === 'describe' ? 'Saving…' : 'Save description'}
          </button>
          {#if describedDirty}
            <button type="button" class="link" onclick={() => (description = profile?.purpose ?? '')}>
              Revert
            </button>
          {/if}
        </div>
      </section>

      <section class="block">
        <h2>Settings</h2>
        {#if draft}
          <label class="field">
            <span>List length</span>
            <input
              type="number"
              min="1"
              step="1"
              value={draft.list_length}
              onchange={(e) => {
                if (draft) draft.list_length = Math.max(1, Number(e.currentTarget.value) || 1);
                touched();
              }}
            />
          </label>
          <label class="field">
            <span>Floor score <small>below this a model is set aside</small></span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={draft.floor_score}
              onchange={(e) => {
                if (draft) draft.floor_score = Number(e.currentTarget.value);
                touched();
              }}
            />
          </label>
          <label class="field" class:off={settingsAbsent}>
            <span>Price sensitivity</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              disabled={settingsAbsent}
              value={draft.price_sensitivity}
              onchange={(e) => {
                if (draft) draft.price_sensitivity = Number(e.currentTarget.value);
                touched();
              }}
            />
          </label>
          <label class="field" class:off={settingsAbsent}>
            <span>Experience weight</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              disabled={settingsAbsent}
              value={draft.experience_weight}
              onchange={(e) => {
                if (draft) draft.experience_weight = Number(e.currentTarget.value);
                touched();
              }}
            />
          </label>
          <label class="switch">
            <input
              type="checkbox"
              checked={draft.auto_apply}
              disabled={busy === 'auto'}
              onchange={(e) => void toggleAuto(e.currentTarget.checked)}
            />
            <span>auto-apply <small>ship on its own when it changes its mind</small></span>
          </label>
          {#if settingsAbsent}
            <p class="hint">
              Price sensitivity and experience weight need the profile settings route, which this
              server does not have yet. Everything else here writes through the routes it does have.
            </p>
          {/if}
        {/if}
      </section>

    </aside>

    <!-- CENTRE: the weights ---------------------------------------------- -->
    <section class="middle">
      <div class="head">
        <h2>Weights</h2>
        <p class="sum mono" class:off={!balanced}>
          sum {sum.toFixed(3)}
          {#if !balanced}
            <button type="button" class="link" onclick={normalise}>normalise</button>
          {/if}
        </p>
      </div>
      <p class="hint">
        One row per axis this seat counts. The value is what it cares about; min and max are the
        room it is allowed to move in, a locked axis holds while the others absorb a change, and ×
        takes the axis off the seat.
      </p>

      {#if loading}
        <p class="hint">Loading…</p>
      {:else}
        {#each rows as [axis, control] (axis)}
          <div class="axis">
            <WeightSlider
              {axis}
              label={described[axis]?.label || axis.replace(/_/g, ' ')}
              value={control.value}
              min={control.min}
              max={control.max}
              locked={control.locked}
              idPrefix="p"
              onchange={(value) => move(axis, value)}
              onlock={(next) => {
                control.locked = next;
                touched();
              }}
            />
            <label class="bound">
              <span>min</span>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                disabled={settingsAbsent}
                value={control.min}
                onchange={(e) => bound(axis, 'min', Number(e.currentTarget.value))}
              />
            </label>
            <label class="bound">
              <span>max</span>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                disabled={settingsAbsent}
                value={control.max}
                onchange={(e) => bound(axis, 'max', Number(e.currentTarget.value))}
              />
            </label>
            <button
              type="button"
              class="drop"
              title={`take ${axis} off this seat`}
              aria-label={`remove ${axis}`}
              onclick={() => removeAxis(axis)}
            >
              ×
            </button>
            {#if described[axis]?.describes}
              <p class="describes">{described[axis]?.describes}</p>
            {/if}
          </div>
        {:else}
          <p class="hint">
            This seat counts nothing yet. Add an axis and it will start ranking.
          </p>
        {/each}

        <div class="acts">
          <button
            type="button"
            class="add"
            aria-expanded={picking}
            onclick={() => (picking = !picking)}
            disabled={available.length === 0}
          >
            {picking ? 'Close' : 'Add axis'}
          </button>
          {#if available.length === 0 && everyAxis.length > 0}
            <span class="hint inline">every axis for this modality is already on the seat</span>
          {/if}
        </div>

        {#if picking}
          <ul class="picker">
            {#each available as axis (axis.name)}
              <li>
                <button type="button" onclick={() => addAxis(axis.name)}>
                  <span class="pick-name">{axis.label || axis.name}</span>
                  <span class="pick-says">{axis.describes}</span>
                </button>
              </li>
            {:else}
              <li class="hint">
                {everyAxis.length
                  ? 'Nothing left to add.'
                  : 'This server did not answer with any axes for this modality.'}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    </section>

    <!-- RIGHT: the list those weights produce ----------------------------- -->
    <section class="list">
      <div class="listhead">
        <span class="label">
          {!engaged ? 'Shipping now' : previewRows ? 'Preview' : 'Would ship'}
          {#if previewing}<span class="spinner" role="status" aria-label="previewing"></span>{/if}
        </span>
        <button
          type="button"
          class="primary"
          onclick={() => void apply()}
          disabled={busy === 'apply' || loading}
        >
          {busy === 'apply' ? 'Applying…' : 'Apply'}
        </button>
        {#if dirty}
          <button type="button" class="link" onclick={discard}>Discard</button>
        {/if}
      </div>

      {#if !engaged && !loading}
        <p class="hint">What this seat is serving. Move a control to see what would change.</p>
      {/if}

      <ol class="live" class:idle={!engaged}>
        {#each shipping as row (row.id)}
          <li
            class:lead={row.rank === 1}
            class:pinned={row.status === 'pinned'}
            animate:flip={{ duration: duration(200, $reducedMotion) }}
          >
            <span class="pos num">{row.rank}</span>
            <span class="id mono">
              {row.id}
              {#if row.local_ids.length}<span class="local mono">{row.local_ids[0]}</span>{/if}
            </span>
            <span class="score num">{engaged ? row.score.toFixed(3) : '—'}</span>
            {#if shippedAt.has(row.id)}
              <span class="was" title="where it sits on the gateway now">#{shippedAt.get(row.id)}</span>
            {:else}
              <span class="was new">new</span>
            {/if}
            <span class="rowacts">
              <button
                type="button"
                disabled={statusAbsent || busy === `status:${row.id}`}
                title={statusAbsent ? 'this server has no per-model status route yet' : 'hold this model at the top'}
                onclick={() => void setStatus(row.id, row.status === 'pinned' ? 'active' : 'pinned')}
              >
                {row.status === 'pinned' ? 'unpin' : 'pin'}
              </button>
              <button
                type="button"
                disabled={statusAbsent || busy === `status:${row.id}`}
                title={statusAbsent ? 'this server has no per-model status route yet' : 'keep this model off this seat'}
                onclick={() => void setStatus(row.id, 'removed')}
              >
                remove
              </button>
            </span>
          </li>
        {:else}
          <li class="hint">{loading ? 'Loading…' : 'Nothing ranks for this profile yet.'}</li>
        {/each}
      </ol>

      {#if dropping.length}
        <ol class="shipped" aria-label="on the gateway now, not in this draft">
          {#each dropping as goneRow (goneRow.id)}
            <li>
              <span class="pos num">#{goneRow.was}</span>
              <span class="id mono">{goneRow.id}</span>
              <span class="was">was shipping</span>
            </li>
          {/each}
        </ol>
      {/if}

      {#if said}
        <p class={said.ok ? 'notice' : 'error'}>{said.text}</p>
      {/if}
    </section>
  </div>

  <!-- what this seat is called, and what it refuses ------------------- -->
  <div class="more">
      <section class="block">
        <h2>Name and life</h2>
        <label class="field">
          <span>Name</span>
          <input bind:value={renaming} pattern="[A-Za-z0-9_\-]+" autocomplete="off" />
        </label>
        <div class="acts">
          <button
            type="button"
            onclick={() => void rename()}
            disabled={busy === 'rename' || renaming.trim() === name || !renaming.trim()}
          >
            {busy === 'rename' ? 'Renaming…' : 'Rename'}
          </button>
        </div>
        <label class="field">
          <span>Copy to <small>a new seat with these weights</small></span>
          <input bind:value={copying} pattern="[A-Za-z0-9_\-]+" placeholder="{name}_cheap" autocomplete="off" />
        </label>
        <div class="acts">
          <button type="button" onclick={() => void copy()} disabled={busy === 'copy' || !copying.trim()}>
            {busy === 'copy' ? 'Copying…' : 'Copy'}
          </button>
          {#if confirming}
            <span class="confirm">
              Delete {name}?
              <button type="button" class="danger" onclick={() => void remove()} disabled={busy === 'delete'}>
                {busy === 'delete' ? 'Deleting…' : 'Yes, delete'}
              </button>
              <button type="button" onclick={() => (confirming = false)}>Keep</button>
            </span>
          {:else if forcing}
            <span class="confirm">
              <button type="button" class="danger" onclick={() => void remove(true)} disabled={busy === 'delete'}>
                {busy === 'delete' ? 'Deleting…' : 'Delete anyway'}
              </button>
              <button type="button" onclick={() => (forcing = false)}>Keep it</button>
            </span>
          {:else}
            <button type="button" onclick={() => (confirming = true)}>Delete</button>
          {/if}
        </div>
        <p class="hint">
          Deleting a profile takes its seat off every gateway the next time Sieve writes. It cannot
          be undone from here.
        </p>
      </section>

      {#if profile}
        <section class="block">
          <h2>What it refuses, what a task costs, when it changes its mind</h2>
          <p class="hint">
            These three save on their own, at once, because they are three decisions and one Apply
            would make them look like one.
          </p>
          <SettingsBlocks
            {profile}
            {token}
            onsaved={(_updated, what) => (said = { ok: true, text: `Saved ${what}.` })}
            onerror={(failure) => (said = { ok: false, text: explainError(failure) })}
          />
        </section>
      {/if}
  </div>

  <!-- ---- the tabs ------------------------------------------------------ -->
  <div class="tabs" role="tablist" aria-label="More about this profile">
    {#each TABS as one (one.id)}
      <button
        type="button"
        role="tab"
        id={`tab-${one.id}`}
        aria-selected={tab === one.id}
        aria-controls={`panel-${one.id}`}
        class:on={tab === one.id}
        onclick={() => (tab = tab === one.id ? null : one.id)}
      >
        {one.label}
      </button>
    {/each}
  </div>

  <div class="panel" id={`panel-${tab ?? 'none'}`} role="tabpanel" aria-labelledby={tab ? `tab-${tab}` : undefined}>
    {#if tab === null}
      <p class="hint">
        Nothing open. Each of these is fetched when you open it — the ranking detail is most of a
        megabyte on a language seat, and a page that asks for it before anybody wants it is a page
        that loads slowly for everybody.
      </p>
    {:else if tab === 'ranking'}
      <h2>What carried each score</h2>
      {#if ranked.length === 0}
        <p class="hint">{rankingNote || (ranking ? 'Nothing ranks for this profile yet.' : 'Asking the server for the ranking…')}</p>
      {:else}
        <div class="scroll-x">
          <table>
            <thead>
              <tr>
                <th class="pos">#</th>
                <th>model</th>
                <th>axes</th>
                <th class="right">score</th>
                <th>conf</th>
                <th class="right">cost</th>
                <th>reach</th>
              </tr>
            </thead>
            <tbody>
              {#each ranked as rank (rank.model_id)}
                <tr class:lead={rank.position === 1}>
                  <td class="pos num">{rank.position}</td>
                  <td>
                    <div class="id mono">{rank.model_id}</div>
                    {#if rank.local_ids?.length}
                      <div class="local mono">{rank.local_ids.join(' · ')}</div>
                    {/if}
                    {#if rank.position === 1 && rank.flip}
                      <div class="flipline">{rank.flip}</div>
                    {/if}
                  </td>
                  <td><AxisBars axes={rank.axes ?? []} weights={weightValues} /></td>
                  <td class="right num">{rank.final.toFixed(3)}</td>
                  <td><ConfDots confidence={rank.confidence} /></td>
                  <td class="right num">
                    {rank.cost_per_task == null ? '—' : `$${rank.cost_per_task.toPrecision(3)}`}
                  </td>
                  <td><span class="dot" class:on={rank.reachable}></span></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if allRanked.length > ranked.length}
          <p class="hint">
            The top {ranked.length} of {allRanked.length} ranked. The rest score below these and are
            not drawn.
          </p>
        {/if}
        {#if setAside.length}
          <ul class="aside">
            {#each setAside as rank (rank.model_id)}
              <li>
                <span class="mono">{rank.model_id}</span>
                <span class="reason">
                  {rank.dominated_by
                    ? `dominated by ${rank.dominated_by}`
                    : `excluded: ${rank.excluded_by}`}
                </span>
              </li>
            {/each}
          </ul>
          {#if allAside.length > setAside.length}
            <p class="hint">and {allAside.length - setAside.length} more set aside.</p>
          {/if}
        {/if}
      {/if}
    {:else if tab === 'history'}
      <h2>History</h2>
      {#if historyNote}<p class="hint">{historyNote}</p>{/if}
      <ul class="log">
        {#each history.slice(0, 50) as entry, index (`${entry.when}-${index}`)}
          <li>
            <span class="who">{entry.who}</span>
            <span class="what">{entry.what}</span>
            <span class="at">{when(entry.when)}</span>
            {#if entry.before !== undefined || entry.after !== undefined}
              <span class="mono change" title={`${show(entry.before)} → ${show(entry.after)}`}>
                {show(entry.before)} → {show(entry.after)}
              </span>
            {/if}
          </li>
        {:else}
          <li class="hint">Nothing recorded.</li>
        {/each}
      </ul>
    {:else if tab === 'experience'}
      <h2>Experience</h2>
      <p class="hint">
        How the models on this seat have actually behaved when your agents called them, over thirty
        days. The rate is smoothed — (successes + 1) / (calls + 2) — so one lucky call does not read
        as a perfect record.
      </p>
      {#if experienceNote}<p class="hint">{experienceNote}</p>{/if}
      <ul class="log">
        {#each experience as row (row.model_id)}
          <li>
            <span class="mono">{row.model_id}</span>
            <span class="num rate">{(row.experience * 100).toFixed(0)}%</span>
            <span class="at">{row.successes}/{row.outcomes} ok</span>
          </li>
        {/each}
      </ul>
    {:else if tab === 'costs'}
      <h2>Cost multipliers</h2>
      <p class="hint">
        What a local id really costs this seat, against its published price. Blank uses the default,
        which is set once on the Connectors screen and shown here in grey.
      </p>
      {#if prefixes.length === 0}
        <p class="hint">
          {defaults === null
            ? 'This server has no cost-multiplier route yet.'
            : 'No prefixes yet: they are read from the local ids the connectors serve.'}
        </p>
      {:else}
        {#each prefixes as prefix (prefix)}
          <label class="multiplier">
            <span class="mono">{prefix}</span>
            <input
              type="number"
              min="0"
              step="0.05"
              disabled={settingsAbsent}
              placeholder={String(defaults?.[prefix] ?? 1)}
              value={draft?.cost_multipliers[prefix] ?? ''}
              onchange={(e) => override(prefix, e.currentTarget.value)}
            />
            <small class="default">default {defaults?.[prefix] ?? 1}</small>
          </label>
        {/each}
        <p class="hint">These ride along with Apply, like every other control on this page.</p>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .back {
    margin: 0 0 0.4rem;
    font-size: 0.76rem;
  }
  .back a {
    color: var(--muted);
    text-decoration: none;
  }
  .back a:hover {
    color: var(--accent);
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.76rem;
    color: var(--muted);
    max-width: 22rem;
    margin-bottom: 0.8rem;
  }
  .token input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
  }

  /*
    One column, in reading order: what the seat is, what shapes its list, the
    weights, then the list itself. It used to be three columns of unequal
    height, which meant the list you came to read started two screens down on
    a laptop and overlapped its own controls on a phone.
  */
  .three,
  .more {
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    max-width: 64rem;
  }
  .more {
    margin-top: 0.8rem;
  }
  .side {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    min-width: 0;
  }
  h1 {
    font-family: var(--display);
    font-size: 1.5rem;
    margin: 0;
    overflow-wrap: anywhere;
  }
  .modality {
    margin: 0;
    color: var(--muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  .chip {
    align-self: flex-start;
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--panel2);
    color: var(--muted);
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
  }
  .chip[data-tone='accent'] {
    color: var(--accent);
    border-color: var(--accent);
  }
  .chip[data-tone='good'] {
    color: var(--good);
  }

  .block,
  .middle,
  .list,
  .panel {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.7rem 0.8rem 0.8rem;
    min-width: 0;
  }
  h2 {
    margin: 0 0 0.35rem;
    font-family: var(--ui);
    font-size: 0.86rem;
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.8rem;
    line-height: 1.45;
    padding: 0.35rem 0.45rem;
    resize: vertical;
  }
  .field,
  .switch,
  .bound,
  .multiplier {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.74rem;
    color: var(--muted);
    margin-top: 0.35rem;
    min-width: 0;
  }
  .field > span,
  .multiplier > span {
    flex: 1;
    min-width: 0;
  }
  .field small,
  .switch small {
    display: block;
    opacity: 0.75;
  }
  .field.off {
    opacity: 0.55;
  }
  .field input,
  .bound input,
  .multiplier input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font: inherit;
    font-size: 0.78rem;
    padding: 0.2rem 0.35rem;
    width: 5.5rem;
    min-width: 0;
  }
  .acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
    margin-top: 0.5rem;
  }
  .confirm {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.74rem;
    color: var(--muted);
  }

  /* ---- centre ---- */
  .middle .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .sum {
    color: var(--muted);
    font-size: 0.74rem;
    margin: 0;
  }
  .sum.off {
    color: var(--bad);
  }
  /*
    label · slider · value · lock come from WeightSlider's own grid; min, max
    and remove are this one's. Fixed columns, so nothing slides under anything
    at any width, and the whole row wraps below 900px rather than squeezing.
  */
  .axis {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 5.5rem 5.5rem 2rem;
    gap: 0.4rem 0.5rem;
    align-items: center;
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--rule);
  }
  .axis .bound {
    justify-content: flex-end;
    margin-top: 0;
  }
  .axis .bound input {
    width: 3.4rem;
  }
  .describes {
    grid-column: 1 / -1;
    margin: 0 0 0.15rem;
    color: var(--muted);
    font-size: 0.7rem;
    line-height: 1.4;
  }
  .drop {
    background: none;
    border: 1px solid transparent;
    border-radius: 5px;
    color: var(--muted);
    font: inherit;
    font-size: 0.9rem;
    line-height: 1;
    padding: 0.1rem 0.35rem;
    cursor: pointer;
  }
  .drop:hover {
    color: var(--bad);
    border-color: var(--rule);
  }
  .picker {
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
    border: 1px solid var(--rule);
    border-radius: 8px;
    max-height: 18rem;
    overflow: auto;
  }
  .picker li + li {
    border-top: 1px solid var(--rule);
  }
  .picker button {
    display: block;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    padding: 0.35rem 0.5rem;
    cursor: pointer;
  }
  .picker button:hover {
    background: var(--panel2);
  }
  .pick-name {
    display: block;
    font-size: 0.8rem;
    text-transform: capitalize;
  }
  .pick-says {
    display: block;
    color: var(--muted);
    font-size: 0.72rem;
    line-height: 1.4;
  }

  /* ---- right ---- */
  .listhead {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-bottom: 0.35rem;
  }
  .listhead .label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--muted);
    margin-right: auto;
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .spinner {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    border: 1.5px solid var(--rule);
    border-top-color: var(--accent);
    animation: spin 700ms linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  ol.live,
  ol.shipped,
  ul.log,
  ul.aside {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  ol.live li {
    display: grid;
    grid-template-columns: 1.5rem minmax(0, 1fr) 3.2rem 2.6rem auto;
    gap: 0.45rem;
    align-items: center;
    padding: 0.22rem 0.3rem;
    border-bottom: 1px solid var(--rule);
  }
  ol.live li.lead {
    background: color-mix(in oklab, var(--accent) 8%, transparent);
    border-radius: 6px;
  }
  ol.live li.hint {
    display: block;
    border-bottom: none;
  }
  ol.live li.pinned .pos {
    color: var(--accent);
  }
  ol.live.idle li {
    opacity: 0.62;
  }
  ol.live.idle li.lead {
    background: none;
  }
  ol.shipped {
    opacity: 0.45;
    margin-top: 0.3rem;
  }
  ol.shipped li {
    display: grid;
    grid-template-columns: 2rem minmax(0, 1fr) auto;
    gap: 0.45rem;
    align-items: center;
    padding: 0.18rem 0.3rem;
    font-size: 0.74rem;
  }
  .pos {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .id {
    font-size: 0.78rem;
    overflow-wrap: anywhere;
  }
  .local {
    display: block;
    color: var(--reach);
    font-size: 0.66rem;
    overflow-wrap: anywhere;
  }
  .score {
    text-align: right;
    font-size: 0.76rem;
  }
  .was {
    color: var(--muted);
    font-size: 0.68rem;
    text-align: right;
  }
  .was.new {
    color: var(--accent);
  }
  .rowacts {
    display: inline-flex;
    gap: 0.2rem;
    opacity: 0;
    transition: opacity 120ms ease;
  }
  ol.live li:hover .rowacts,
  ol.live li:focus-within .rowacts {
    opacity: 1;
  }
  .rowacts button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 5px;
    color: var(--muted);
    font: inherit;
    font-size: 0.66rem;
    padding: 0.02rem 0.3rem;
    cursor: pointer;
  }
  .rowacts button:hover:not(:disabled) {
    color: var(--ink);
    border-color: var(--accent);
  }

  /* ---- tabs ---- */
  .tabs {
    display: flex;
    gap: 0.3rem;
    flex-wrap: wrap;
    margin: 1rem 0 0;
  }
  .tabs button {
    background: var(--panel);
    border: 1px solid var(--rule);
    border-bottom: none;
    border-radius: 8px 8px 0 0;
    color: var(--muted);
    font: inherit;
    font-size: 0.76rem;
    padding: 0.25rem 0.7rem;
    cursor: pointer;
  }
  .tabs button.on {
    color: var(--ink);
    border-color: var(--accent);
  }
  .panel {
    border-radius: 0 8px 8px 8px;
  }

  /* ---- tables and logs ---- */
  .scroll-x {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.76rem;
  }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 400;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.4rem;
    border-bottom: 1px solid var(--rule);
  }
  td {
    padding: 0.28rem 0.4rem;
    border-bottom: 1px solid var(--rule);
    vertical-align: top;
  }
  tr.lead td {
    background: color-mix(in oklab, var(--accent) 7%, transparent);
  }
  th.right,
  td.right {
    text-align: right;
  }
  .flipline {
    color: var(--accent);
    font-size: 0.7rem;
  }
  .dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--rule);
  }
  .dot.on {
    background: var(--reach);
  }
  ul.log li {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr) auto;
    gap: 0.4rem;
    align-items: baseline;
    padding: 0.2rem 0.1rem;
    border-bottom: 1px solid var(--rule);
    font-size: 0.75rem;
  }
  .who {
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .at {
    color: var(--muted);
    font-size: 0.7rem;
    text-align: right;
  }
  .change {
    grid-column: 1 / -1;
    color: var(--muted);
    font-size: 0.68rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .rate {
    text-align: right;
  }
  ul.aside {
    margin-top: 0.5rem;
    opacity: 0.75;
  }
  ul.aside li {
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
    font-size: 0.72rem;
    padding: 0.12rem 0.1rem;
  }
  .reason {
    color: var(--muted);
  }
  .default {
    color: var(--muted);
    opacity: 0.75;
    font-size: 0.68rem;
  }

  /* ---- shared ---- */
  button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.74rem;
    padding: 0.15rem 0.6rem;
    cursor: pointer;
  }
  button.primary {
    border-color: var(--accent);
  }
  button.link {
    background: none;
    border-color: transparent;
    color: var(--muted);
    text-decoration: underline;
    padding: 0 0.2rem;
  }
  button.danger {
    border-color: var(--bad);
    color: var(--bad);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .hint {
    color: var(--muted);
    font-size: 0.74rem;
    line-height: 1.45;
    margin: 0 0 0.4rem;
  }
  .hint.inline {
    margin: 0;
  }
  .notice {
    color: var(--good);
    font-size: 0.74rem;
    margin: 0.35rem 0 0;
  }
  .error {
    color: var(--bad);
    font-size: 0.74rem;
    margin: 0.35rem 0 0;
    overflow-wrap: anywhere;
  }

  @media (max-width: 900px) {
    .axis {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 2rem;
    }
    .axis :global(.slider) {
      grid-column: 1 / -1;
    }
    .rowacts {
      opacity: 1;
    }
  }
</style>
