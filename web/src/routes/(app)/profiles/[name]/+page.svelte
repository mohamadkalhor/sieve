<script lang="ts">
  /**
   * One profile, tuned.
   *
   * Every control is in the left column; the right column is the list those
   * controls produce, which is the whole answer.
   *
   *   Mode          auto -- the weights rank, pins go first, removals stay out
   *                 manual -- exactly the models picked by hand, in that order
   *   Must support  a model ships only if it is *known* to do each checked thing
   *   Add models    (manual) search the reachable models and add one
   *   Weights       (auto) presets, then one row per axis: a slider, an exact
   *                 percentage, a lock that holds it while the others move
   *   How many      (auto) at least one, no upper bound; pins count towards it
   *   Removed       (auto) what was taken off the list, to put back
   *   Find a model  (auto) search every reachable model: where it ranks, and
   *                 pin, remove or restore it from there
   *   Prefix weights  per router prefix, a multiplier on the score of every
   *                 model it serves -- apart from cost; blank is 1
   *
   * Axes keep the order they were added in. A list that re-sorted itself by
   * weight on every drag moved the row out from under the pointer.
   *
   * WHAT IT WRITES
   *
   *   any control         PUT /v1/profiles/{name}/settings, 400 ms after the last input
   *   Ship now            POST /v1/profiles/{name}/apply
   *   copy · rename · delete   POST /v1/profiles · PATCH · DELETE
   *
   * The list comes from POST /v1/profiles/{name}/preview. It has three honest
   * states and one fact: ranking, the box is busy, it failed -- and "nothing
   * ships", which may only be said when an answer actually came back empty.
   */
  import { goto } from '$app/navigation';
  import {
    NEEDS,
    api,
    explainError,
    type ApiError,
    type AxisRow,
    type HistoryRow,
    type Listed,
    type Need,
    type ProfileMode,
    type ProfileSettings
  } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import { ago } from '$lib/freshness';
  import { runPulse } from '$lib/refresh.svelte';
  import { session } from '$lib/session.svelte';
  import {
    COST_AXIS,
    DEBOUNCE_MS,
    PRESETS,
    SHIP_MIN,
    SLOW_MS,
    addAxis,
    applyPreset,
    barWidth,
    clampShip,
    listState,
    removeAxis,
    renormalise,
    shift,
    shipState,
    toggle,
    type Preset
  } from '$lib/profile/tune';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  const name = $derived(data.name);

  const NEED_LABEL: Record<Need, string> = {
    vision: 'Image input',
    reasoning: 'Thinking mode',
    tools: 'Tool calling',
    structured_output: 'Structured output'
  };
  const NEED_TAG: Record<Need, string> = {
    vision: 'vision',
    reasoning: 'thinking',
    tools: 'tools',
    structured_output: 'JSON'
  };

  let profile = $state<Profile | null>(null);
  let chain = $state<Chain | null>(null);
  let everyAxis = $state<AxisRow[]>([]);

  /* -- the settings, as the page holds them ------------------------------ */

  let weights = $state<Record<string, number>>({});
  /** axis order: the order they were added in, never re-sorted by weight */
  let order = $state<string[]>([]);
  let locked = $state<string[]>([]);
  let loaded = $state<Record<string, number>>({});
  let ship = $state(4);
  let mode = $state<ProfileMode>('auto');
  let manual = $state<string[]>([]);
  let pinned = $state<string[]>([]);
  let removed = $state<string[]>([]);
  let needs = $state<Need[]>([]);
  /** this profile's own multiplier per router prefix; a prefix not here uses the default */
  let prefixWeights = $state<Record<string, number>>({});
  /** the router prefixes this box reaches, from the Connectors page's list */
  let prefixes = $state<string[]>([]);
  let finding = $state('');

  let loading = $state(true);
  let gone = $state<ApiError | null>(null);
  let said = $state<{ ok: boolean; text: string } | null>(null);

  /* -- the list ---------------------------------------------------------- */

  let models = $state<Listed[] | null>(null);
  let next = $state<Listed[]>([]);
  let blocked = $state<Listed[]>([]);
  let removedRows = $state<Listed[]>([]);
  let missing = $state<{ id: string; name: string }[]>([]);
  let pool = $state<Listed[]>([]);
  /** router ids that matched nothing in the catalogue: pinnable once linked */
  let unlinked = $state<{ local_id: string; name: string }[]>([]);
  /** the search shows only models no source has scored */
  let onlyUnscored = $state(false);
  let linking = $state<string | null>(null);
  let pending = $state(false);
  let waitedMs = $state(0);
  let failed = $state<string | null>(null);
  let showMore = $state(false);
  let shipping = $state(false);

  /* -- the menu and the picker ------------------------------------------ */

  let menuOpen = $state(false);
  let asking = $state<'' | 'copy' | 'rename' | 'delete'>('');
  let askName = $state('');
  let history = $state<HistoryRow[] | null>(null);
  let adding = $state(false);
  let search = $state('');

  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  const options = $derived({ token: session.token || undefined });

  const labels = $derived(
    Object.fromEntries(everyAxis.map((axis: AxisRow) => [axis.name, axis.label || axis.name]))
  );
  const meanings = $derived(
    Object.fromEntries(everyAxis.map((axis) => [axis.name, axis.meaning || axis.describes || '']))
  );
  const spare = $derived(everyAxis.filter((axis: AxisRow) => !(axis.name in weights)));
  const shown = $derived(order.filter((axis) => axis in weights));

  const byId = $derived(Object.fromEntries(pool.map((row) => [row.id, row])));
  /** whether any reachable model here carries capability data at all */
  const describable = $derived(
    pool.some((row) => Object.values(row.abilities ?? {}).some((v) => v !== null))
  );
  const supportCount = $derived(
    Object.fromEntries(
      NEEDS.map((need) => [need, pool.filter((row) => row.abilities?.[need] === true).length])
    ) as Record<Need, number>
  );
  const unscoredCount = $derived(
    pool.filter((row) => row.scored === false).length + unlinked.length
  );
  /** router ids with no catalogue entry, matching a search */
  function unlinkedMatching(needle: string, anyway: boolean) {
    if (!needle && !anyway) return [];
    return unlinked
      .filter((u) => !needle || u.local_id.toLowerCase().includes(needle))
      .slice(0, 12);
  }
  const picks = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return pool
      .filter((row) => !manual.includes(row.id))
      .filter((row) => !onlyUnscored || row.scored === false)
      .filter((row) => needs.every((need) => row.abilities?.[need] === true))
      .filter(
        (row) =>
          !needle ||
          row.name.toLowerCase().includes(needle) ||
          row.id.toLowerCase().includes(needle)
      )
      .slice(0, 8);
  });

  const shipped = $derived(chain ? [chain.primary, ...(chain.fallbacks ?? [])] : []);
  const listing = $derived(listState({ pending, waitedMs, error: failed, models }));
  const button = $derived(
    shipState({
      shipped,
      next: (models ?? []).map((row: Listed) => row.id),
      shipping,
      ready: listing === 'ready'
    })
  );

  /* ---------------------------------------------------------------------- */
  /* loading                                                                 */
  /* ---------------------------------------------------------------------- */

  function lockKey(): string {
    return `sieve:locks:${name}`;
  }

  $effect(() => {
    runPulse.seen();
    const wanted = name;
    loading = true;
    models = null;
    failed = null;
    history = null;
    void (async () => {
      const [p, s, c] = await Promise.all([
        api.profile(wanted),
        api.profileSettings(wanted),
        api.chain(wanted)
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
      chain = c.ok ? c.value : null;

      const held: ProfileSettings | null = s.ok && s.value?.weights ? s.value : null;
      weights = held ? { ...held.weights } : { ...p.value.weights };
      loaded = { ...weights };
      order = Object.keys(weights);
      ship = clampShip(held ? held.ship : (p.value.ship ?? 4));
      mode = held?.mode ?? 'auto';
      manual = [...(held?.manual ?? [])];
      pinned = [...(held?.pinned ?? [])];
      removed = [...(held?.removed ?? [])];
      needs = [...(held?.needs ?? [])];
      prefixWeights = { ...(held?.prefix_weights ?? {}) };
      try {
        const stored = JSON.parse(localStorage.getItem(lockKey()) ?? '[]');
        locked = Array.isArray(stored) ? stored.filter((a) => a in weights) : [];
      } catch {
        locked = [];
      }
      loading = false;

      const [axes, prices] = await Promise.all([
        api.axes(p.value.modality),
        api.costMultipliers(options)
      ]);
      if (wanted !== name) return;
      if (axes.ok && Array.isArray(axes.value)) everyAxis = axes.value;
      if (prices.ok && prices.value) prefixes = Object.keys(prices.value).sort();

      await refresh();
    })();
  });

  /* ---------------------------------------------------------------------- */
  /* the list                                                                */
  /* ---------------------------------------------------------------------- */

  let timer: ReturnType<typeof setTimeout> | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let inflight = 0;

  function patch() {
    return {
      weights,
      ship,
      mode,
      manual,
      pinned,
      removed,
      needs,
      prefix_weights: prefixWeights,
      remove_axes: everyAxis.map((a: AxisRow) => a.name).filter((a: string) => !(a in weights))
    };
  }

  async function refresh(): Promise<void> {
    const wanted = name;
    const mine = ++inflight;
    pending = true;
    failed = null;
    waitedMs = 0;
    const started = Date.now();
    ticker ??= setInterval(() => (waitedMs = Date.now() - started), 500);

    const result = await api.preview(wanted, patch(), options);
    if (wanted !== name || mine !== inflight) return;

    if (ticker) {
      clearInterval(ticker);
      ticker = null;
    }
    pending = false;
    waitedMs = 0;
    if (!result.ok) {
      failed = explainError(result.error);
      return;
    }
    const value = result.value;
    models = value?.models ?? [];
    next = value?.next ?? [];
    blocked = value?.blocked ?? [];
    removedRows = value?.removed ?? [];
    missing = value?.missing ?? [];
    pool = value?.pool ?? pool;
    unlinked = value?.unlinked ?? unlinked;
  }

  function touched(): void {
    said = null;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      void save();
      void refresh();
    }, DEBOUNCE_MS);
  }

  async function save(): Promise<void> {
    const wanted = name;
    const result = await api.saveProfileSettings(wanted, patch(), options);
    if (wanted !== name) return;
    if (!result.ok) said = { ok: false, text: explainError(result.error) };
  }

  /* ---------------------------------------------------------------------- */
  /* the controls                                                            */
  /* ---------------------------------------------------------------------- */

  function setMode(value: ProfileMode): void {
    if (value === mode) return;
    // a hand-made list starts from what ships now, rather than from nothing
    if (value === 'manual' && manual.length === 0 && models?.length) {
      manual = models.map((row) => row.id);
    }
    mode = value;
    touched();
  }

  function setPrefixWeight(prefix: string, raw: string): void {
    const value = Number(raw);
    const rest = Object.fromEntries(Object.entries(prefixWeights).filter(([key]) => key !== prefix));
    // blank, or 1, is what an unnamed prefix already counts as
    prefixWeights =
      raw.trim() === '' || !Number.isFinite(value) || value < 0 || value === 1
        ? rest
        : { ...rest, [prefix]: value };
    touched();
  }

  /** a search over every reachable model, with where each one stands */
  const found = $derived.by(() => {
    const needle = finding.trim().toLowerCase();
    if (!needle && !onlyUnscored) return [];
    return pool
      .map((row, index) => ({ row, rank: index + 1 }))
      .filter(({ row }) => !onlyUnscored || row.scored === false)
      .filter(
        ({ row }) =>
          !needle ||
          row.name.toLowerCase().includes(needle) ||
          row.id.toLowerCase().includes(needle) ||
          row.local_ids.some((id) => id.toLowerCase().includes(needle))
      )
      .slice(0, onlyUnscored ? 40 : 12);
  });
  const foundUnlinked = $derived(unlinkedMatching(finding.trim().toLowerCase(), onlyUnscored));
  const picksUnlinked = $derived(unlinkedMatching(search.trim().toLowerCase(), onlyUnscored));

  /**
   * A router id that matched nothing has no catalogue id, and a profile holds
   * catalogue ids. Linking makes one (no scores), then the pin or add goes on.
   */
  async function linkThen(localId: string, then: (id: string) => void): Promise<void> {
    if (!profile || linking) return;
    linking = localId;
    const result = await api.linkUnscored(
      { local_id: localId, modality: profile.modality, name: localId.split('/').pop() },
      options
    );
    linking = null;
    if (!result.ok || !result.value) {
      said = {
        ok: false,
        text: result.ok ? 'Could not link that model.' : explainError(result.error)
      };
      return;
    }
    unlinked = unlinked.filter((u) => u.local_id !== localId);
    then(result.value.model_id);
  }

  function standing(id: string): string {
    const at = (models ?? []).findIndex((row) => row.id === id);
    if (removed.includes(id)) return 'removed';
    if (at >= 0) return pinned.includes(id) ? `pinned · ships #${at + 1}` : `ships #${at + 1}`;
    if (pinned.includes(id)) return 'pinned · skipped';
    if (needs.length && (byId[id]?.lacks ?? []).length) return 'fails Must support';
    return 'not shipping';
  }

  function toggleNeed(need: Need): void {
    needs = needs.includes(need) ? needs.filter((n) => n !== need) : [...needs, need];
    touched();
  }

  function move(axis: string, value: number): void {
    const hold = new Set(locked.filter((a) => a !== axis));
    weights = renormalise(weights, axis, value, hold);
    touched();
  }

  function exact(axis: string, percent: string): void {
    const value = Number(percent);
    if (!Number.isFinite(value)) return;
    move(axis, value / 100);
  }

  function lock(axis: string): void {
    locked = toggle(locked, axis);
    try {
      localStorage.setItem(lockKey(), JSON.stringify(locked));
    } catch {
      /* a remembered lock is a convenience, not state */
    }
  }

  function preset(kind: Preset | 'reset'): void {
    weights = kind === 'reset' ? { ...loaded } : applyPreset(weights, kind);
    if (kind === 'reset') order = Object.keys(loaded);
    touched();
  }

  function drop(axis: string): void {
    if (Object.keys(weights).length <= 1) {
      said = { ok: false, text: 'A profile needs at least one axis to score by.' };
      return;
    }
    weights = removeAxis(weights, axis);
    order = order.filter((a) => a !== axis);
    locked = locked.filter((a) => a !== axis);
    touched();
  }

  function add(axis: string): void {
    weights = addAxis(weights, axis);
    order = [...order.filter((a) => a !== axis), axis];
    adding = false;
    touched();
  }

  function setShip(value: number): void {
    const wanted = clampShip(value);
    if (wanted === ship) return;
    ship = wanted;
    touched();
  }

  /* -- rows -------------------------------------------------------------- */

  function pin(id: string): void {
    pinned = toggle(pinned, id);
    removed = removed.filter((held) => held !== id);
    touched();
  }

  function remove(id: string): void {
    removed = removed.includes(id) ? removed : [...removed, id];
    pinned = pinned.filter((held) => held !== id);
    touched();
  }

  function restore(id: string): void {
    removed = removed.filter((held) => held !== id);
    touched();
  }

  function addManual(id: string): void {
    if (!manual.includes(id)) manual = [...manual, id];
    search = '';
    touched();
  }

  function dropManual(id: string): void {
    manual = manual.filter((held) => held !== id);
    touched();
  }

  function nudge(id: string, by: number): void {
    const index = manual.indexOf(id);
    if (index < 0) return;
    manual = shift(manual, index, by);
    touched();
  }

  /** the manual list as it reads: every listed id, shipping or not */
  const manualRows = $derived(
    manual.map((id) => {
      const ships = (models ?? []).find((row) => row.id === id);
      const kept = blocked.find((row) => row.id === id);
      const gone = missing.find((row) => row.id === id);
      return {
        id,
        row: ships ?? kept ?? byId[id] ?? null,
        name: ships?.name ?? kept?.name ?? gone?.name ?? byId[id]?.name ?? id,
        ships: Boolean(ships),
        lacks: kept?.lacks ?? [],
        unreachable: Boolean(gone)
      };
    })
  );

  /* ---------------------------------------------------------------------- */
  /* shipping and the menu                                                   */
  /* ---------------------------------------------------------------------- */

  async function shipNow(): Promise<void> {
    if (button.disabled) return;
    shipping = true;
    said = null;
    if (timer) clearTimeout(timer);
    await save();
    const result = await api.applyProfile(name, options);
    shipping = false;
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    chain = result.value?.chain ?? chain;
    now = new Date();
    const combo = (result.value?.combos ?? []).join(', ');
    said = { ok: true, text: combo ? `Shipped as ${combo}.` : 'Shipped.' };
    void refresh();
  }

  function openMenu(what: '' | 'copy' | 'rename' | 'delete'): void {
    menuOpen = false;
    asking = what;
    askName = what === 'copy' ? `${name}_copy` : name;
  }

  async function openHistory(): Promise<void> {
    menuOpen = false;
    if (history) {
      history = null;
      return;
    }
    const result = await api.history(name, options);
    history = result.ok && Array.isArray(result.value) ? result.value : [];
  }

  async function doCopy(): Promise<void> {
    const wanted = askName.trim();
    if (!wanted || !profile) return;
    const result = await api.newProfile(
      { name: wanted, modality: profile.modality, from: name, copy_from: name },
      options
    );
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    asking = '';
    await goto(`/profiles/${encodeURIComponent(wanted)}`);
  }

  async function doRename(): Promise<void> {
    const wanted = askName.trim();
    if (!wanted || wanted === name) {
      asking = '';
      return;
    }
    const result = await api.renameProfile(name, wanted, options);
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    asking = '';
    await goto(`/profiles/${encodeURIComponent(wanted)}`);
  }

  async function doDelete(): Promise<void> {
    let result = await api.removeProfile(name, false, options);
    if (!result.ok && result.error.code === 'in_use') {
      result = await api.removeProfile(name, true, options);
    }
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    await goto('/profiles');
  }

  const pct = (value: number): string => (value * 100).toFixed(0);
</script>

<svelte:head><title>{name} · Sieve</title></svelte:head>

<svelte:window
  onclick={(event) => {
    const target = event.target as HTMLElement | null;
    if (menuOpen && !target?.closest('.menu')) menuOpen = false;
  }}
/>

{#snippet tags(row: Listed | null)}
  {#if row?.abilities || row?.scored === false}
    <span class="tags">
      {#if row?.scored === false}<span
          class="tag unscored"
          title="No source has benchmarked it: it ranks on price alone">no score</span
        >{/if}
      {#each NEEDS as need (need)}
        {#if row?.abilities?.[need] === true}<span class="tag">{NEED_TAG[need]}</span>{/if}
      {/each}
    </span>
  {/if}
{/snippet}

{#snippet unscoredFilter()}
  <div class="filters">
    <button
      type="button"
      class="chip"
      class:on={onlyUnscored}
      aria-pressed={onlyUnscored}
      title="Models no source has benchmarked"
      onclick={() => (onlyUnscored = !onlyUnscored)}>Unscored only · {unscoredCount}</button
    >
    {#if onlyUnscored}<a class="sub" href="/unscored">score them</a>{/if}
  </div>
{/snippet}

{#snippet unlinkedRow(
  u: { local_id: string; name: string },
  label: string,
  then: (id: string) => void
)}
  <li class="found">
    <span class="mono found-rank">—</span>
    <span class="pick-who">
      <span class="pick-name">{u.name}</span>
      <span class="found-meta">
        <span class="mono">{u.local_id}</span>
        <span class="tag unscored" title="The catalogue has no entry for this router id yet"
          >unknown</span
        >
      </span>
    </span>
    <button
      type="button"
      class="link"
      disabled={linking !== null}
      title="Give it a catalogue entry with no scores, then {label.toLowerCase()} it"
      onclick={() => linkThen(u.local_id, then)}
      >{linking === u.local_id ? 'Linking…' : label}</button
    >
  </li>
{/snippet}

{#snippet pinIcon(on: boolean)}
  <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
    <path
      d="M6 1.5h4l-.6 4 2.6 2.5v1.2H9v5.3l-1 .5-1-.5V9.2H4V8l2.6-2.5z"
      fill={on ? 'currentColor' : 'none'}
      stroke="currentColor"
      stroke-width="1.2"
      stroke-linejoin="round"
    />
  </svg>
{/snippet}

<a class="back" href="/profiles">
  <span aria-hidden="true">←</span> All profiles
</a>

{#if loading}
  <p class="muted">Loading…</p>
{:else if gone}
  <h1 class="display name">{name}</h1>
  <p class="muted">{explainError(gone)}</p>
{:else if profile}
  <header class="head">
    <div class="who">
      <h1 class="display name">{profile.name}</h1>
      <p class="purpose">{profile.purpose}</p>
    </div>
    <div class="acts">
      <button
        type="button"
        class="primary wide"
        disabled={button.disabled}
        title={button.title}
        onclick={shipNow}>{button.label}</button
      >
      <div class="menu">
        <button
          type="button"
          class="dots"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="More"
          onclick={() => (menuOpen = !menuOpen)}>···</button
        >
        {#if menuOpen}
          <div class="sheet" role="menu">
            <button type="button" role="menuitem" onclick={() => openMenu('copy')}>Copy</button>
            <button type="button" role="menuitem" onclick={() => openMenu('rename')}>Rename</button>
            <button type="button" role="menuitem" onclick={() => openMenu('delete')}>Delete</button>
            <button type="button" role="menuitem" onclick={openHistory}>History</button>
          </div>
        {/if}
      </div>
    </div>
  </header>

  {#if asking === 'copy' || asking === 'rename'}
    <div class="ask">
      <label>
        <span>{asking === 'copy' ? 'Copy to' : 'Rename to'}</span>
        <input bind:value={askName} spellcheck="false" autocomplete="off" />
      </label>
      <button
        type="button"
        class="primary"
        onclick={() => (asking === 'copy' ? doCopy() : doRename())}
        >{asking === 'copy' ? 'Copy' : 'Rename'}</button
      >
      <button type="button" class="quiet" onclick={() => (asking = '')}>Cancel</button>
    </div>
  {:else if asking === 'delete'}
    <div class="ask">
      <span>Delete {name}? The combo it ships stops being updated.</span>
      <button type="button" class="danger" onclick={doDelete}>Delete</button>
      <button type="button" class="quiet" onclick={() => (asking = '')}>Cancel</button>
    </div>
  {/if}

  {#if said}
    <p class:bad={!said.ok} class="said">{said.text}</p>
  {/if}

  <div class="columns">
    <div class="controls">
      <!-- mode -->
      <section class="panel">
        <h2>How the list is made</h2>
        <div class="segment" role="radiogroup" aria-label="List mode">
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'auto'}
            aria-label="Auto: ranked by weights"
            class:on={mode === 'auto'}
            onclick={() => setMode('auto')}
          >
            <strong>Auto</strong>
            <span>ranked by weights</span>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'manual'}
            aria-label="Manual: picked by hand"
            class:on={mode === 'manual'}
            onclick={() => setMode('manual')}
          >
            <strong>Manual</strong>
            <span>picked by hand</span>
          </button>
        </div>
      </section>

      <!-- needs -->
      <section class="panel">
        <h2>Must support</h2>
        {#if describable}
          <p class="sub">a model ships only if its source says it can</p>
          <div class="checks">
            {#each NEEDS as need (need)}
              <label class="check">
                <input
                  type="checkbox"
                  checked={needs.includes(need)}
                  onchange={() => toggleNeed(need)}
                />
                <span class="check-name">{NEED_LABEL[need]}</span>
                <span class="mono check-count">{supportCount[need]}/{pool.length}</span>
              </label>
            {/each}
          </div>
        {:else if pool.length}
          <p class="sub">no source describes what these models can do</p>
        {:else}
          <p class="sub">waiting for the list…</p>
        {/if}
      </section>

      {#if mode === 'manual'}
        <!-- the picker -->
        <section class="panel">
          <h2>Add models</h2>
          <p class="sub">
            {manual.length} in the list · no limit{needs.length ? ' · filtered by Must support' : ''}
          </p>
          <input
            class="search"
            type="search"
            placeholder="Search reachable models…"
            bind:value={search}
            spellcheck="false"
            autocomplete="off"
          />
          {@render unscoredFilter()}
          <ul class="picks">
            {#each picks as row (row.id)}
              <li>
                <button type="button" class="pick" onclick={() => addManual(row.id)}>
                  <span class="pick-who">
                    <span class="pick-name">{row.name}</span>
                    {@render tags(row)}
                  </span>
                  <span class="mono pick-score">{row.score.toFixed(2)}</span>
                  <span class="plus" aria-hidden="true">+</span>
                </button>
              </li>
            {:else}
              {#if !picksUnlinked.length}
                <li class="sub none">
                  {pool.length ? 'nothing else matches' : 'waiting for the list…'}
                </li>
              {/if}
            {/each}
            {#each picksUnlinked as u (u.local_id)}
              {@render unlinkedRow(u, 'Add', addManual)}
            {/each}
          </ul>
        </section>
      {:else}
        <!-- find a model -->
        <section class="panel">
          <h2>Find a model</h2>
          <p class="sub">where it ranks for this profile, and pin or remove it</p>
          <input
            class="search"
            type="search"
            placeholder="Search {pool.length || ''} reachable models…"
            bind:value={finding}
            spellcheck="false"
            autocomplete="off"
          />
          {@render unscoredFilter()}
          {#if finding.trim() || onlyUnscored}
            <ul class="picks">
              {#each found as { row, rank } (row.id)}
                {@const state = standing(row.id)}
                <li class="found">
                  <span class="mono found-rank">#{rank}</span>
                  <span class="pick-who">
                    <span class="pick-name">{row.name}</span>
                    <span class="found-meta">
                      <span class="mono">{row.score.toFixed(2)}</span>
                      {#if row.scored === false}<span class="tag unscored">no score</span>{/if}
                      <span class:ships={state.includes('ships')} class:gone={state === 'removed'}>{state}</span>
                    </span>
                  </span>
                  {#if removed.includes(row.id)}
                    <button type="button" class="link" onclick={() => restore(row.id)}>Restore</button>
                  {:else}
                    <span class="found-acts">
                      <button
                        type="button"
                        class="icon"
                        class:active={pinned.includes(row.id)}
                        aria-pressed={pinned.includes(row.id)}
                        title={pinned.includes(row.id) ? 'Unpin' : 'Pin: always ship, first'}
                        aria-label={`${pinned.includes(row.id) ? 'Unpin' : 'Pin'} ${row.name}`}
                        onclick={() => pin(row.id)}>{@render pinIcon(pinned.includes(row.id))}</button
                      >
                      <button
                        type="button"
                        class="icon"
                        title="Remove: never ship"
                        aria-label={`Remove ${row.name}`}
                        onclick={() => remove(row.id)}>✕</button
                      >
                    </span>
                  {/if}
                </li>
              {:else}
                {#if !foundUnlinked.length}
                  <li class="sub none">no reachable model matches</li>
                {/if}
              {/each}
              {#each foundUnlinked as u (u.local_id)}
                {@render unlinkedRow(u, 'Pin', (id) => pin(id))}
              {/each}
            </ul>
          {/if}
        </section>

        <!-- weights -->
        <section class="panel weights">
          <div class="panel-head">
            <h2>Weights</h2>
            <span class="sub">move one, the unlocked others follow</span>
          </div>

          <div class="presets">
            {#each PRESETS as item (item.id)}
              <button
                type="button"
                class="chip"
                title={item.says}
                disabled={item.id !== 'even' && !(COST_AXIS in weights)}
                onclick={() => preset(item.id)}>{item.label}</button
              >
            {/each}
            <button type="button" class="chip" title="the weights this page opened with" onclick={() => preset('reset')}
              >Reset</button
            >
          </div>

          {#each shown as axis (axis)}
            <div class="axis" class:held={locked.includes(axis)}>
              <div class="axis-top">
                <div class="axis-who">
                  <div class="axis-name">{labels[axis] ?? axis}</div>
                  {#if meanings[axis]}<div class="axis-meaning">{meanings[axis]}</div>{/if}
                </div>
                <div class="axis-right">
                  <label class="exact">
                    <input
                      class="mono"
                      type="number"
                      min="0"
                      max="100"
                      step="1"
                      aria-label={`${labels[axis] ?? axis}, percent`}
                      value={pct(weights[axis])}
                      disabled={locked.includes(axis)}
                      onchange={(event) => exact(axis, event.currentTarget.value)}
                    /><span>%</span>
                  </label>
                  <button
                    type="button"
                    class="icon"
                    class:active={locked.includes(axis)}
                    aria-pressed={locked.includes(axis)}
                    title={locked.includes(axis) ? 'Unlock: let it follow the others' : 'Lock: hold this share'}
                    aria-label={`Lock ${labels[axis] ?? axis}`}
                    onclick={() => lock(axis)}
                  >
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                      <rect x="3" y="7" width="10" height="7" rx="1.5" fill={locked.includes(axis) ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.3" />
                      <path d={locked.includes(axis) ? 'M5.5 7V5a2.5 2.5 0 0 1 5 0v2' : 'M5.5 7V5a2.5 2.5 0 0 1 4.9-.7'} fill="none" stroke="currentColor" stroke-width="1.3" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    class="icon"
                    aria-label={`Remove ${labels[axis] ?? axis}`}
                    title="Remove this axis"
                    onclick={() => drop(axis)}>✕</button
                  >
                </div>
              </div>
              <input
                class="slider"
                type="range"
                min="0"
                max="1"
                step="0.01"
                aria-label={labels[axis] ?? axis}
                value={weights[axis]}
                disabled={locked.includes(axis)}
                style:--share={barWidth(weights[axis])}
                oninput={(event) => move(axis, Number(event.currentTarget.value))}
              />
            </div>
          {/each}

          <div class="add">
            {#if adding}
              <select
                aria-label="Add an axis"
                onchange={(event) => {
                  const picked = event.currentTarget.value;
                  if (picked) add(picked);
                }}
              >
                <option value="">choose an axis…</option>
                {#each spare as axis (axis.name)}
                  <option value={axis.name}>{axis.label || axis.name}</option>
                {/each}
              </select>
              <button type="button" class="quiet" onclick={() => (adding = false)}>Cancel</button>
            {:else}
              <button
                type="button"
                class="ghost"
                disabled={spare.length === 0}
                title={spare.length === 0 ? 'every axis for this modality is already weighted' : ''}
                onclick={() => (adding = true)}>+ Add an axis</button
              >
            {/if}
          </div>
        </section>

        <!-- how many -->
        <section class="panel howmany">
          <div>
            <h2>How many to ship</h2>
            <p class="sub">
              the first is used, the rest are fallbacks{pinned.length ? ` · ${pinned.length} pinned` : ''}
            </p>
          </div>
          <div class="stepper">
            <button
              type="button"
              aria-label="One fewer"
              disabled={ship <= SHIP_MIN}
              onclick={() => setShip(ship - 1)}>−</button
            >
            <input
              class="display count"
              type="number"
              min={SHIP_MIN}
              aria-label="How many to ship"
              value={ship}
              onchange={(event) => setShip(Number(event.currentTarget.value))}
            />
            <button type="button" aria-label="One more" onclick={() => setShip(ship + 1)}>+</button>
          </div>
        </section>

        {#if removed.length}
          <section class="panel">
            <h2>Removed</h2>
            <p class="sub">never shipped, however they score</p>
            <ul class="plain">
              {#each removed as id (id)}
                {@const row = removedRows.find((r) => r.id === id) ?? byId[id]}
                <li class="restorable">
                  <span class="pick-name">{row?.name ?? id}</span>
                  <button type="button" class="link" onclick={() => restore(id)}>Restore</button>
                </li>
              {/each}
            </ul>
          </section>
        {/if}
      {/if}

      <!-- per-seat prefix weights -->
      <section class="panel">
        <div class="panel-head">
          <h2>Prefix weights</h2>
          <span class="sub">
            multiplies the score of every model a router serves, apart from cost ·
            1.5 lifts, 0.5 halves, blank is 1 · a model on several routers takes its best one
          </span>
        </div>
        {#if prefixes.length}
          <ul class="plain multipliers">
            {#each prefixes as prefix (prefix)}
              {@const own = prefix in prefixWeights}
              <li class="multiplier" class:own>
                <span class="mono prefix">{prefix}</span>
                <span class="mono default">{own ? `×${prefixWeights[prefix]}` : ''}</span>
                <input
                  class="mono"
                  type="number"
                  min="0"
                  step="0.1"
                  placeholder="1"
                  aria-label={`Weight for ${prefix} on this profile`}
                  value={own ? prefixWeights[prefix] : ''}
                  onchange={(event) => setPrefixWeight(prefix, event.currentTarget.value)}
                />
                <button
                  type="button"
                  class="icon"
                  aria-label={`Reset ${prefix} to 1`}
                  title="Reset to 1"
                  disabled={!own}
                  onclick={() => setPrefixWeight(prefix, '')}>↺</button
                >
              </li>
            {/each}
          </ul>
        {:else}
          <p class="sub">no router prefixes are reachable yet</p>
        {/if}
      </section>

      <button
        type="button"
        class="primary phone-ship"
        disabled={button.disabled}
        title={button.title}
        onclick={shipNow}>{button.label}</button
      >
    </div>

    <!-- the answer -->
    <section class="panel list">
      <div class="list-head">
        <h2>What ships</h2>
        <span class="when">
          {#if chain}shipped {ago(new Date(chain.computed_at), now)}{:else}never shipped{/if}
        </span>
      </div>

      {#if listing === 'ranking' && models === null}
        <p class="state">Ranking…</p>
      {:else if listing === 'busy'}
        <p class="state">
          The box is ranking as fast as it can — every slot has been busy for
          {Math.round(SLOW_MS / 1000)} seconds.
          <button type="button" class="link" onclick={() => refresh()}>Try again</button>
        </p>
      {:else if listing === 'error'}
        <p class="state bad">
          {failed}
          <button type="button" class="link" onclick={() => refresh()}>Try again</button>
        </p>
      {:else if mode === 'manual'}
        {#if manual.length === 0}
          <p class="state">The list is empty. Add models from the left.</p>
        {/if}
        {#each manualRows as item, index (item.id)}
          <div class="row" class:out={!item.ships} class:stale={pending}>
            <span class="mono rank">{index + 1}</span>
            <div class="who-cell">
              <div class="model-name">{item.name}</div>
              <div class="model-meta">
                {#if item.unreachable}
                  <span class="warn">not reachable — skipped</span>
                {:else if item.lacks.length}
                  <span class="warn">
                    skipped — not known to support {item.lacks.map((n) => NEED_TAG[n]).join(', ')}
                  </span>
                {:else}
                  <span class="mono model-id">{item.row?.local_ids[0] ?? item.id}</span>
                {/if}
                {@render tags(item.row)}
              </div>
            </div>
            <div class="score-cell">
              <div class="bar">
                <div class="fill" style:width={barWidth(item.row?.score ?? 0)}></div>
              </div>
              <span class="mono number">{item.row ? item.row.score.toFixed(2) : '—'}</span>
            </div>
            <div class="row-acts">
              <button type="button" class="icon" aria-label="Move up" disabled={index === 0} onclick={() => nudge(item.id, -1)}>↑</button>
              <button type="button" class="icon" aria-label="Move down" disabled={index === manual.length - 1} onclick={() => nudge(item.id, 1)}>↓</button>
              <button type="button" class="icon" aria-label={`Remove ${item.name}`} onclick={() => dropManual(item.id)}>✕</button>
            </div>
          </div>
        {/each}
      {:else if listing === 'empty' && !blocked.length}
        <p class="state">
          Nothing ships: no reachable model {needs.length ? 'supports everything checked' : 'scores on these weights'}.
        </p>
      {:else}
        {#each models ?? [] as row, index (row.id)}
          <div class="row" class:pinned={row.pinned} class:stale={pending}>
            <span class="mono rank">{index + 1}</span>
            <div class="who-cell">
              <div class="model-name">{row.name}</div>
              <div class="model-meta">
                <span class="mono model-id">{row.local_ids[0] ?? row.id}</span>
                {@render tags(row)}
              </div>
            </div>
            <div class="score-cell">
              <div class="bar"><div class="fill" style:width={barWidth(row.score)}></div></div>
              <span class="mono number">{row.score.toFixed(2)}</span>
            </div>
            <div class="row-acts">
              <button
                type="button"
                class="icon"
                class:active={row.pinned}
                aria-pressed={row.pinned}
                title={row.pinned ? 'Unpin' : 'Pin: always ship, first'}
                aria-label={`${row.pinned ? 'Unpin' : 'Pin'} ${row.name}`}
                onclick={() => pin(row.id)}>{@render pinIcon(Boolean(row.pinned))}</button
              >
              <button
                type="button"
                class="icon"
                title="Remove from the list"
                aria-label={`Remove ${row.name}`}
                onclick={() => remove(row.id)}>✕</button
              >
            </div>
          </div>
        {/each}

        {#each blocked as row (row.id)}
          <div class="row out">
            <span class="mono rank">·</span>
            <div class="who-cell">
              <div class="model-name">{row.name}</div>
              <div class="model-meta">
                <span class="warn">
                  pinned, skipped — not known to support {(row.lacks ?? []).map((n) => NEED_TAG[n]).join(', ')}
                </span>
              </div>
            </div>
            <span class="mono number">{row.score.toFixed(2)}</span>
            <div class="row-acts">
              <button type="button" class="icon active" title="Unpin" aria-label={`Unpin ${row.name}`} onclick={() => pin(row.id)}>{@render pinIcon(true)}</button>
            </div>
          </div>
        {/each}

        {#each missing as row (row.id)}
          <div class="row out">
            <span class="mono rank">·</span>
            <div class="who-cell">
              <div class="model-name">{row.name}</div>
              <div class="model-meta"><span class="warn">pinned, not reachable — skipped</span></div>
            </div>
            <span></span>
            <div class="row-acts">
              <button type="button" class="icon active" title="Unpin" aria-label={`Unpin ${row.name}`} onclick={() => pin(row.id)}>{@render pinIcon(true)}</button>
            </div>
          </div>
        {/each}

        {#if next.length}
          <div class="divider"><span>next in line</span></div>
          {#each showMore ? next : next.slice(0, 3) as row, index (row.id)}
            <div class="row after" class:stale={pending}>
              <span class="mono rank">{(models?.length ?? 0) + index + 1}</span>
              <div class="who-cell">
                <div class="model-name">{row.name}</div>
                <div class="model-meta">
                  <span class="mono model-id">{row.local_ids[0] ?? row.id}</span>
                  {@render tags(row)}
                </div>
              </div>
              <div class="score-cell">
                <div class="bar"><div class="fill" style:width={barWidth(row.score)}></div></div>
                <span class="mono number">{row.score.toFixed(2)}</span>
              </div>
              <div class="row-acts">
                <button
                  type="button"
                  class="icon"
                  title="Pin: always ship, first"
                  aria-label={`Pin ${row.name}`}
                  onclick={() => pin(row.id)}>{@render pinIcon(false)}</button
                >
                <button
                  type="button"
                  class="icon"
                  title="Remove: never ship"
                  aria-label={`Remove ${row.name}`}
                  onclick={() => remove(row.id)}>✕</button
                >
              </div>
            </div>
          {/each}
          {#if next.length > 3}
            <button type="button" class="more" onclick={() => (showMore = !showMore)}
              >{showMore ? 'show fewer' : `show ${next.length - 3} more`}</button
            >
          {/if}
        {/if}
      {/if}
    </section>
  </div>

  {#if history}
    <section class="panel history">
      <div class="list-head">
        <h2>History</h2>
        <button type="button" class="link" onclick={() => (history = null)}>close</button>
      </div>
      {#if history.length === 0}
        <p class="state">Nothing has changed this profile yet.</p>
      {:else}
        <ul>
          {#each history.slice(0, 40) as row (row.when + row.what)}
            <li>
              <span class="mono when">{ago(new Date(row.when), now)}</span>
              <span class="what">{row.what}</span>
              <span class="who">{row.who}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
{/if}

<style>
  .muted {
    color: var(--muted);
  }
  .display {
    font-family: var(--display);
    font-weight: 500;
  }
  .mono {
    font-family: var(--mono);
  }

  /* -- back and header --------------------------------------------------- */

  .back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--muted);
    text-decoration: none;
    margin-bottom: 14px;
    padding: 4px 0;
  }
  .back:hover {
    color: var(--ink);
  }

  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 22px;
  }
  .who {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .name {
    font-size: 30px;
    line-height: 1.1;
    margin: 0;
  }
  .purpose {
    font-size: 14px;
    color: var(--muted);
    max-width: 62ch;
    margin: 0;
  }
  .acts {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  .primary {
    background: var(--accent);
    color: var(--bg);
    border: none;
    border-radius: 7px;
    padding: 9px 18px;
    font: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  .primary:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .menu {
    position: relative;
  }
  .dots {
    width: 36px;
    height: 36px;
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--muted);
    font: inherit;
    font-size: 16px;
    letter-spacing: 1px;
    cursor: pointer;
  }
  .sheet {
    position: absolute;
    right: 0;
    top: 42px;
    z-index: 5;
    display: flex;
    flex-direction: column;
    min-width: 9rem;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--panel);
    padding: 4px;
  }
  .sheet button {
    text-align: left;
    background: none;
    border: none;
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 8px 10px;
    border-radius: 7px;
    cursor: pointer;
  }
  .sheet button:hover {
    background: var(--panel2);
  }

  .ask {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--panel);
    padding: 12px 14px;
    margin-bottom: 16px;
    font-size: 13px;
  }
  .ask label {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
  }
  .ask input,
  .search {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 6px 9px;
  }
  .quiet,
  .danger {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 6px 12px;
    cursor: pointer;
  }
  .danger {
    border-color: var(--bad);
    color: var(--bad);
  }
  .said {
    font-size: 13px;
    color: var(--good);
    margin: 0 0 14px;
  }
  .said.bad {
    color: var(--bad);
  }

  /* -- the two columns --------------------------------------------------- */

  .columns {
    display: grid;
    grid-template-columns: 380px minmax(0, 1fr);
    gap: 22px;
    align-items: start;
  }
  .controls {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .panel {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 16px 18px;
  }
  .panel h2 {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
  }
  .panel-head {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .sub {
    font-size: 12px;
    color: var(--muted);
    margin: 2px 0 0;
  }

  /* -- mode --------------------------------------------------------------- */

  .segment {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    padding: 4px;
    margin-top: 12px;
    border: 1px solid var(--rule);
    border-radius: 9px;
    background: var(--panel2);
  }
  .segment button {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    border: 1px solid transparent;
    border-radius: 6px;
    background: none;
    color: var(--muted);
    font: inherit;
    padding: 8px 10px;
    cursor: pointer;
    text-align: left;
  }
  .segment strong {
    font-size: 14px;
    font-weight: 600;
  }
  .segment span {
    font-size: 11px;
  }
  .segment button.on {
    background: var(--panel);
    border-color: var(--rule);
    color: var(--ink);
  }
  .segment button.on strong {
    color: var(--accent);
  }
  .segment button:focus-visible,
  .icon:focus-visible,
  .chip:focus-visible,
  .pick:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* -- needs -------------------------------------------------------------- */

  .checks {
    display: flex;
    flex-direction: column;
    margin-top: 8px;
  }
  .check {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid var(--rule);
    font-size: 14px;
    cursor: pointer;
  }
  .check:first-child {
    border-top: none;
  }
  .check input {
    width: 16px;
    height: 16px;
    margin: 0;
    accent-color: var(--accent);
    cursor: pointer;
  }
  .check-count {
    font-size: 11px;
    color: var(--muted);
  }

  /* -- picker ------------------------------------------------------------- */

  .search {
    width: 100%;
    box-sizing: border-box;
    margin-top: 12px;
    padding: 9px 11px;
    font-size: 14px;
  }
  .picks,
  .plain {
    list-style: none;
    margin: 8px 0 0;
    padding: 0;
  }
  .pick {
    width: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 20px;
    align-items: center;
    gap: 10px;
    background: none;
    border: none;
    border-top: 1px solid var(--rule);
    color: var(--ink);
    font: inherit;
    text-align: left;
    padding: 9px 2px;
    cursor: pointer;
  }
  .picks li:first-child .pick {
    border-top: none;
  }
  .pick:hover {
    background: var(--panel2);
  }
  .pick-who {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }
  .pick-name {
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pick-score {
    font-size: 12px;
    color: var(--muted);
  }
  .plus {
    color: var(--accent);
    font-size: 18px;
    text-align: center;
  }
  .none {
    padding: 8px 0;
  }
  .restorable {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid var(--rule);
  }
  .restorable:first-child {
    border-top: none;
  }

  /* -- weights ----------------------------------------------------------- */

  .weights {
    display: flex;
    flex-direction: column;
  }
  .presets {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 12px 0 4px;
  }
  .chip {
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 12px;
    padding: 5px 11px;
    cursor: pointer;
  }
  .chip:hover:not(:disabled) {
    border-color: var(--accent);
  }
  .chip.on {
    border-color: var(--accent);
    color: var(--accent);
  }
  .filters {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 8px 0 2px;
  }
  .tag.unscored {
    color: var(--warn);
    border-color: currentColor;
  }
  .chip:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .axis {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px 0;
    border-bottom: 1px solid var(--rule);
  }
  .axis-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .axis-who {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .axis-name {
    font-size: 14px;
    font-weight: 600;
  }
  .axis-meaning {
    font-size: 12px;
    color: var(--muted);
  }
  .axis-right {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }
  .exact {
    display: flex;
    align-items: center;
    gap: 2px;
    font-size: 12px;
    color: var(--muted);
    margin-right: 4px;
  }
  .exact input {
    width: 3.2em;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font-size: 13px;
    padding: 3px 5px;
    text-align: right;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  .exact input::-webkit-inner-spin-button,
  .exact input::-webkit-outer-spin-button,
  .count::-webkit-inner-spin-button,
  .count::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  .exact input:disabled {
    opacity: 0.7;
  }
  .axis.held .axis-name::after {
    content: ' · locked';
    font-weight: 400;
    font-size: 12px;
    color: var(--accent);
  }

  .icon {
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    background: none;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--muted);
    font: inherit;
    font-size: 13px;
    padding: 0;
    cursor: pointer;
  }
  .icon:hover:not(:disabled) {
    color: var(--ink);
    background: var(--panel2);
  }
  .icon.active {
    color: var(--accent);
  }
  .icon:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .slider {
    -webkit-appearance: none;
    appearance: none;
    width: 100%;
    height: 24px;
    background: transparent;
    cursor: pointer;
    margin: 0;
  }
  .slider:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
  .slider::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 2px;
    background: linear-gradient(
      to right,
      var(--accent) 0 var(--share, 0%),
      var(--panel2) var(--share, 0%) 100%
    );
  }
  .slider::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: var(--panel2);
  }
  .slider::-moz-range-progress {
    height: 4px;
    border-radius: 2px;
    background: var(--accent);
  }
  .slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    margin-top: -7px;
    border-radius: 50%;
    background: var(--ink);
    border: 2px solid var(--bg);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .slider::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--ink);
    border: 2px solid var(--bg);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .slider:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
  }

  .add {
    padding-top: 12px;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .ghost,
  .add select {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 7px 12px;
    cursor: pointer;
  }
  .ghost:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  /* -- how many ---------------------------------------------------------- */

  .howmany {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .stepper {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .stepper button {
    width: 34px;
    height: 34px;
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 18px;
    cursor: pointer;
  }
  .stepper button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .count {
    width: 2.4em;
    font-size: 24px;
    text-align: center;
    background: none;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--ink);
    padding: 0;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  .count:hover,
  .count:focus {
    border-color: var(--rule);
    outline: none;
  }

  .phone-ship {
    display: none;
  }

  /* -- multipliers ------------------------------------------------------- */

  .multipliers {
    margin-top: 8px;
  }
  .multiplier {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 5.5em auto;
    align-items: center;
    gap: 10px;
    padding: 6px 0;
    border-top: 1px solid var(--rule);
    font-size: 13px;
  }
  .multiplier:first-child {
    border-top: none;
  }
  .prefix {
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .default {
    font-size: 11px;
    color: var(--muted);
  }
  .multiplier.own .default {
    color: var(--accent);
  }
  .found {
    display: grid;
    grid-template-columns: 2.6em minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid var(--rule);
  }
  .picks .found:first-child {
    border-top: none;
  }
  .found-rank {
    font-size: 12px;
    color: var(--muted);
  }
  .found-meta {
    display: flex;
    gap: 8px;
    font-size: 11px;
    color: var(--muted);
  }
  .found-meta .ships {
    color: var(--good);
  }
  .found-meta .gone {
    color: var(--warn);
  }
  .found-acts {
    display: flex;
    gap: 2px;
  }
  .multiplier.own .prefix {
    color: var(--accent);
  }
  .multiplier input {
    width: 100%;
    box-sizing: border-box;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font-size: 13px;
    padding: 4px 6px;
    text-align: right;
  }
  .multiplier.own input {
    border-color: var(--accent);
  }

  /* -- the list ---------------------------------------------------------- */

  .list {
    display: flex;
    flex-direction: column;
    padding-bottom: 14px;
  }
  .list-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 6px;
  }
  .when {
    font-size: 12px;
    color: var(--muted);
  }
  .state {
    font-size: 13px;
    color: var(--muted);
    padding: 14px 0 2px;
    border-top: 1px solid var(--rule);
    margin: 0;
  }
  .state.bad {
    color: var(--bad);
  }
  .link {
    background: none;
    border: none;
    color: var(--reach);
    font: inherit;
    font-size: 13px;
    padding: 0 0 0 6px;
    cursor: pointer;
  }

  .row {
    display: grid;
    grid-template-columns: 24px minmax(0, 1fr) 190px auto;
    gap: 16px;
    align-items: center;
    padding: 12px 0;
    border-top: 1px solid var(--rule);
    transition: opacity 120ms;
  }
  .row.stale {
    opacity: 0.6;
  }
  .row.pinned .rank {
    color: var(--accent);
  }
  .row.pinned {
    box-shadow: inset 2px 0 0 var(--accent);
    padding-left: 8px;
    margin-left: -8px;
  }
  .row.out .model-name {
    color: var(--muted);
    text-decoration: line-through;
    text-decoration-color: var(--rule);
  }
  .rank {
    font-size: 13px;
    color: var(--accent);
  }
  .who-cell {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }
  .model-name {
    font-size: 15px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .model-meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 8px;
    min-width: 0;
  }
  .model-id {
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }
  .warn {
    font-size: 12px;
    color: var(--warn);
  }
  .tags {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .tag {
    font-size: 10px;
    line-height: 1;
    color: var(--muted);
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 3px 5px;
  }
  .score-cell {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 34px;
    align-items: center;
    gap: 10px;
  }
  .bar {
    height: 8px;
    background: var(--panel2);
    border-radius: 4px;
    overflow: hidden;
  }
  .fill {
    height: 8px;
    background: var(--accent);
  }
  .number {
    font-size: 12px;
    text-align: right;
  }
  .row-acts {
    display: flex;
    gap: 2px;
    justify-content: flex-end;
  }

  .divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
    padding-top: 12px;
    border-top: 1px dashed var(--rule);
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .after {
    color: var(--muted);
  }
  .after .rank {
    color: var(--muted);
  }
  .after .model-name {
    font-weight: 400;
  }
  .after .fill {
    background: var(--rule);
  }
  .after .row-acts {
    opacity: 0.65;
  }
  .after:hover .row-acts {
    opacity: 1;
  }

  .more {
    text-align: left;
    background: none;
    border: none;
    border-top: 1px solid var(--rule);
    color: var(--reach);
    font: inherit;
    font-size: 13px;
    padding: 12px 0 2px;
    cursor: pointer;
  }

  /* -- history ----------------------------------------------------------- */

  .history {
    margin-top: 18px;
  }
  .history ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .history li {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr) 8rem;
    gap: 12px;
    padding: 10px 0;
    border-top: 1px solid var(--rule);
    font-size: 13px;
  }
  .history .when,
  .history .who {
    color: var(--muted);
    font-size: 12px;
  }

  /* -- narrower ---------------------------------------------------------- */

  @media (max-width: 1180px) {
    .row {
      grid-template-columns: 24px minmax(0, 1fr) 120px auto;
    }
  }

  @media (max-width: 900px) {
    .columns {
      grid-template-columns: minmax(0, 1fr);
      gap: 14px;
    }
    .name {
      font-size: 26px;
    }
    .purpose {
      font-size: 13px;
    }
    .wide {
      display: none;
    }
    .phone-ship {
      display: block;
      width: 100%;
      min-height: 44px;
      padding: 12px;
      font-size: 15px;
    }
    .panel {
      padding: 14px;
    }
    .slider {
      height: 40px;
    }
    .slider::-webkit-slider-thumb {
      width: 24px;
      height: 24px;
      margin-top: -10px;
    }
    .slider::-moz-range-thumb {
      width: 24px;
      height: 24px;
    }
    .icon {
      width: 36px;
      height: 36px;
    }
    .stepper button {
      width: 44px;
      height: 44px;
      font-size: 20px;
    }
    .ghost {
      width: 100%;
      min-height: 44px;
      text-align: center;
      font-size: 14px;
    }
    .row {
      grid-template-columns: 18px minmax(0, 1fr) auto;
      grid-template-areas:
        'rank who acts'
        'rank score score';
      gap: 6px 10px;
    }
    .rank {
      grid-area: rank;
      align-self: start;
      padding-top: 2px;
    }
    .who-cell {
      grid-area: who;
    }
    .row-acts {
      grid-area: acts;
    }
    .score-cell,
    .row > .number {
      grid-area: score;
    }
    .row > .number {
      text-align: left;
    }
    .history li {
      grid-template-columns: minmax(0, 1fr);
      gap: 2px;
    }
  }
</style>
