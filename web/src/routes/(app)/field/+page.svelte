<script lang="ts">
  /** Field: every measured model at a glance, one axis against what it costs. */
  import { goto } from '$app/navigation';
  import { api, type ApiError, type ModelRow } from '$lib/api/client';
  import type {
    Axis,
    Chain,
    Leaderboard as LeaderboardData,
    Modality,
    Profile,
    Ranking
  } from '$lib/types';
  import {
    BLEND_IN,
    BLEND_OUT,
    hasSidedPrices,
    mark,
    postedPerMillion,
    rawPerMillion
  } from '$lib/field';
  import Empty from '$lib/components/Empty.svelte';
  import FieldSearch from '$lib/components/FieldSearch.svelte';
  import Kpi from '$lib/components/Kpi.svelte';
  import Leaderboard from '$lib/components/Leaderboard.svelte';
  import Scatter, { type Line, type Point } from '$lib/components/Scatter.svelte';

  let modalities = $state<{ modality: Modality; models: number }[]>([]);
  let modality = $state<Modality>('llm');
  let axes = $state<Axis[]>([]);
  let profiles = $state<Profile[]>([]);
  let models = $state<ModelRow[]>([]);
  let rankings = $state<Record<string, Ranking>>({});
  let axisName = $state<string>('');
  let board = $state<LeaderboardData | null>(null);
  let metric = $state<string | undefined>(undefined);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);

  /** the two searches */
  let provider = $state('');
  let model = $state('');

  /**
   * Whose eyes the Field is looking through.
   *
   * `raw` is the data as published. Every quality score is the same number
   * whichever profile computed it -- checked on 952 models across 11 axes,
   * identical in all nine profiles -- and the cost is the posted price. Nothing
   * on the chart owes anything to a preset.
   *
   * A profile name is that profile at work: the cost of one of *its* tasks, its
   * own final score as the vertical axis, and the chain it chose ringed. This
   * used to be a quiet "of cheap_bulk" beside the cost axis, on by default, so
   * the chart moved when a profile was picked and nothing on screen said the
   * raw data had never been showing in the first place.
   */
  const RAW = 'raw';
  let view = $state<string>(RAW);
  const profileView = $derived(view !== RAW);

  /** Raw view only: which posted number is the cost. */
  type Price = 'blended' | 'input' | 'output';
  let price = $state<Price>('blended');

  /** The vertical axis a profile view adds: that profile's own weighted verdict. */
  const SCORE = '__score__';

  const DEFAULT_AXIS: Record<string, string> = { llm: 'intelligence' };

  /** Only axes at least one profile actually weights: the rest are noise here. */
  const usedAxes = $derived.by(() => {
    const weighted = new Set(profiles.flatMap((p) => Object.keys(p.weights)));
    return axes.filter((axis) => weighted.has(axis.name));
  });

  /** The #1 of every profile: what "holding a seat" counts, in any view. */
  const primaries = $derived(
    new Set(
      Object.values(rankings)
        .map((ranking) => ranking.ranks?.find((rank) => rank.position === 1)?.model_id)
        .filter((id): id is string => Boolean(id))
    )
  );

  /** The chain the chosen profile holds, fetched when a profile is picked. */
  let chain = $state<Chain | null>(null);
  $effect(() => {
    const which = view;
    chain = null;
    if (which === RAW) return;
    api.chain(which).then((result) => {
      if (which === view) chain = result.ok ? result.value : null;
    });
  });

  /** Ringed on the chart: every profile's #1 in raw view, one profile's pick otherwise. */
  const seats = $derived(profileView ? new Set(chain ? [chain.primary] : []) : primaries);
  const fallbacks = $derived(new Set(profileView ? (chain?.fallbacks ?? []) : []));

  /**
   * Cost per task, from **the chosen profile's** ranking.
   *
   * There is no such thing as the cost of a task. `reader` sends 200k input
   * tokens and `cheap_bulk` sends 2k, so the same model differs by two orders
   * of magnitude between them.
   *
   * A model that profile did not cost is left off, not placed at a nominal
   * 10k-in / 1k-out. That nominal shape belonged to no profile, so in a view
   * named after one it would have been a number presented as that profile's
   * when it was nobody's.
   */
  const costs = $derived.by(() => {
    const out = new Map<string, number>();
    for (const rank of rankings[view]?.ranks ?? []) {
      if (rank.cost_per_task != null) out.set(rank.model_id, rank.cost_per_task);
    }
    return out;
  });

  /** Whether that cost came from the model's own traffic or the declared shape. */
  const costFrom = $derived.by(() => {
    const out = new Map<string, 'shape' | 'telemetry'>();
    for (const rank of rankings[view]?.ranks ?? []) {
      if (rank.cost_from) out.set(rank.model_id, rank.cost_from);
    }
    return out;
  });

  const values = $derived.by(() => {
    const out = new Map<string, number>();
    if (axisName === SCORE) {
      for (const rank of rankings[view]?.ranks ?? []) out.set(rank.model_id, rank.final);
      return out;
    }
    // identical in every ranking (see `view`), so taking the first is not a choice
    for (const ranking of Object.values(rankings)) {
      for (const rank of ranking.ranks ?? []) {
        const axis = rank.axes?.find((a) => a.axis === axisName);
        if (axis?.value != null && !out.has(rank.model_id)) out.set(rank.model_id, axis.value);
      }
    }
    return out;
  });

  const marked = $derived(mark(models, { provider, model }));
  const searching = $derived(Boolean(provider.trim() || model.trim()));

  function costOf(row: ModelRow): number | null {
    if (profileView) return costs.get(row.id) ?? null;
    if (price === 'blended') return postedPerMillion(row.price);
    return rawPerMillion(row.price, price);
  }

  const points = $derived<Point[]>(
    models.map((row) => ({
      id: row.id,
      x: costOf(row),
      y: values.get(row.id) ?? null,
      reachable: row.reachable,
      primary: seats.has(row.id),
      fallback: fallbacks.has(row.id),
      effort: row.effort,
      lit: marked.lit.has(row.id),
      dim: searching && !marked.lit.has(row.id),
      hidden: marked.drawn !== null && !marked.drawn.has(row.id),
      // a price list has nothing to measure, so it makes no claim either way
      measured: profileView ? costFrom.get(row.id) === 'telemetry' : undefined
    }))
  );

  const lines = $derived<Line[]>(
    [...marked.lines].map(([family, modes]) => ({ family, ids: modes.map((m) => m.id) }))
  );

  function chooseView(next: string) {
    view = next;
    if (next === RAW) {
      if (axisName === SCORE) axisName = DEFAULT_AXIS[modality] ?? usedAxes[0]?.name ?? '';
    } else {
      // a profile view opens on the profile's own verdict: that is what it is for
      axisName = SCORE;
    }
  }

  async function load(which: Modality) {
    loading = true;
    error = null;

    /*
      The board is fetched beside this, not inside it: it is slow, and it is
      drawn under the chart or not at all, so the chart does not wait on it.
      An answer for a tab that has since been left is dropped.
    */
    board = null;
    api.leaderboard(which, metric).then((result) => {
      if (which !== modality) return;
      board = result.ok ? result.value : null;
    });

    const [axesResult, profilesResult, modelsResult] = await Promise.all([
      api.axes(which),
      api.profiles(which),
      api.models({ modality: which, limit: 1000 })
    ]);

    if (!axesResult.ok) error = axesResult.error;
    axes = axesResult.ok ? axesResult.value : [];
    profiles = profilesResult.ok ? profilesResult.value : [];
    models = modelsResult.ok ? modelsResult.value.items : [];
    if (!modelsResult.ok) error = modelsResult.error;

    const wanted = usedAxes.map((a) => a.name);
    if (axisName !== SCORE || view === RAW) {
      axisName = wanted.includes(axisName) ? axisName : (DEFAULT_AXIS[which] ?? wanted[0] ?? '');
    }

    const loaded: Record<string, Ranking> = {};
    await Promise.all(
      profiles.map(async (profile) => {
        const found = await api.ranking(profile.name);
        if (found.ok) loaded[profile.name] = found.value;
      })
    );
    rankings = loaded;
    loading = false;
  }

  $effect(() => {
    api.modalities().then((result) => {
      if (result.ok) {
        modalities = result.value;
        if (result.value.length && !result.value.some((m) => m.modality === modality)) {
          modality = result.value[0].modality;
        }
      }
    });
  });

  $effect(() => {
    // A modality change resets everything that belonged to the last one: the
    // metric (`elo:with_vocals` means nothing outside music), the search, and
    // the view -- a text profile has nothing to say about video.
    void modality;
    metric = undefined;
    provider = '';
    model = '';
    view = RAW;
    price = 'blended';
  });

  $effect(() => {
    void load(modality);
  });

  async function chooseMetric(next: string) {
    const which = modality;
    metric = next;
    const again = await api.leaderboard(which, next);
    // the same guard as `load`: only answer for the tab still on screen
    if (which === modality && next === metric && again.ok) board = again.value;
  }

  /**
   * Is a quality-against-cost scatter answerable for this modality?
   *
   * The server decides, against a named threshold, because it is the side that
   * knows how many models carry a price.
   */
  const showScatter = $derived(board?.scatter_ok !== false);

  const matched = $derived(models.filter((m) => m.reachable).length);

  /** Do the posted prices here even have two sides to separate? */
  const sided = $derived(hasSidedPrices(models));
  /** what a media price is counted in, for the axis title */
  const unit = $derived(models.find((m) => m.price?.unit)?.price?.unit ?? 'unit');

  const xLabel = $derived.by(() => {
    if (profileView) return `cost of one ${view} task (USD)`;
    if (!sided) return `posted price per ${unit} (USD)`;
    if (price === 'blended') return `posted price per 1M tokens, ${BLEND_IN / BLEND_OUT}:1 in:out (USD)`;
    return `posted ${price} price per 1M tokens (USD)`;
  });

  const yLabel = $derived(
    axisName === SCORE
      ? `${view} score`
      : (usedAxes.find((a) => a.name === axisName)?.label ?? axisName)
  );

  /** A choice this population cannot offer falls back rather than plotting nothing. */
  $effect(() => {
    if (!sided && price !== 'blended') price = 'blended';
  });
</script>

<svelte:head><title>Field · Sieve</title></svelte:head>

<header class="top">
  <h1>Field</h1>
  <p class="lede">
    Every model anyone has measured, against what it costs you. Lit points are ones you can
    reach; the ringed ones hold a seat today.
  </p>
</header>

<div class="tabs scroll-x" role="tablist" aria-label="Modality">
  {#each modalities as entry (entry.modality)}
    <button
      role="tab"
      aria-selected={entry.modality === modality}
      class:on={entry.modality === modality}
      onclick={() => (modality = entry.modality)}
    >
      {entry.modality}<span class="count num">{entry.models}</span>
    </button>
  {/each}
</div>

<div class="kpis scroll-x">
  <Kpi label="measured" value={models.length} />
  <Kpi label="reachable" value={matched} tone="reach" />
  <Kpi label="profiles" value={profiles.length} />
  <Kpi label="holding a seat" value={primaries.size} tone="accent" />
</div>

{#if showScatter}
  <!--
    One row: what you look through, what you measure, what it costs, and who.
    The sentences that used to sit under it -- what the axis describes, how the
    cost was built, how many families charge one rate -- are gone. They were
    true and they buried the chart; the axis titles now sit on the chart itself.
  -->
  <div class="controls">
    <label class="ctl">
      <span>View</span>
      <select id="view" value={view} onchange={(e) => chooseView(e.currentTarget.value)}>
        <option value={RAW}>Raw data</option>
        {#if profiles.length}
          <optgroup label="As a profile sees it">
            {#each [...profiles].sort((a, b) => a.name.localeCompare(b.name)) as p (p.name)}
              <option value={p.name}>{p.name}</option>
            {/each}
          </optgroup>
        {/if}
      </select>
    </label>

    <label class="ctl">
      <span>Quality</span>
      <select id="axis" bind:value={axisName}>
        {#if profileView}
          <option value={SCORE}>{view} score</option>
        {/if}
        {#each usedAxes as axis (axis.name)}
          <option value={axis.name}>{axis.label || axis.name}</option>
        {/each}
      </select>
    </label>

    {#if !profileView && sided}
      <label class="ctl">
        <span>Price</span>
        <select id="price" bind:value={price}>
          <option value="blended">blended per 1M</option>
          <option value="input">input per 1M</option>
          <option value="output">output per 1M</option>
        </select>
      </label>
    {/if}

    <FieldSearch
      {models}
      {provider}
      {model}
      matched={marked.lit.size}
      onchange={(next) => {
        provider = next.provider;
        model = next.model;
      }}
    />
  </div>
{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if showScatter && points.some((p) => p.y !== null)}
  <Scatter
    {points}
    {lines}
    {xLabel}
    {yLabel}
    seatLabel={profileView ? `${view}'s pick` : 'current #1'}
    onselect={(id) => goto(`/rankings?model=${encodeURIComponent(id)}`)}
  />
  {#if board && (board.rows ?? []).length > 0}
    <!-- a modality with both: the ranking sits under the chart -->
    <div class="under"><Leaderboard {board} onmetric={chooseMetric} /></div>
  {/if}
{:else if board}
  <!--
    No cost axis worth plotting. Same screen, same tab, a different chart,
    because a different question is the one that can be answered here.
  -->
  <Leaderboard {board} onmetric={chooseMetric} />
{:else}
  <Empty
    {error}
    title="Nothing plotted yet"
    hint={models.length
      ? 'Models are in the catalogue but no ranking has been computed, so no axis values exist yet.'
      : ''}
  />
{/if}

<style>
  .under {
    margin-top: 1.75rem;
  }
  .top {
    margin-bottom: 1rem;
  }
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 0;
    max-width: 62ch;
  }
  .tabs {
    display: flex;
    gap: 0.35rem;
    margin-bottom: 0.9rem;
    padding-bottom: 0.2rem;
  }
  .tabs button {
    background: var(--panel);
    border: 1px solid var(--rule);
    color: var(--muted);
    border-radius: 999px;
    padding: 0.25rem 0.7rem;
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .tabs button.on {
    color: var(--ink);
    border-color: var(--accent);
  }
  .count {
    color: var(--muted);
    margin-left: 0.4rem;
    font-size: 0.72rem;
  }
  .kpis {
    display: flex;
    gap: 0.6rem;
    margin-bottom: 1rem;
    padding-bottom: 0.2rem;
  }
  /*
    One row that wraps rather than four that stack. Each control is a column of
    (label, input) so the labels line up across the row, and every control has
    the same `flex` rule so a wrap puts whole controls on the next line instead
    of orphaning a label from its select.
  */
  .controls {
    display: flex;
    align-items: flex-end;
    gap: 0.55rem 0.7rem;
    flex-wrap: wrap;
    margin-bottom: 0.55rem;
  }
  .controls :global(.ctl) {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    flex: 1 1 9rem;
    min-width: 0;
  }
  .controls :global(.ctl > span) {
    color: var(--muted);
    font-size: 0.75rem;
    white-space: nowrap;
  }
  .controls :global(.ctl > select),
  .controls :global(.ctl > input) {
    width: 100%;
    min-width: 0;
  }
  /* the two search boxes earn more room than a select, and `of` needs least */
  .controls :global(.ctl.wide) {
    flex: 1 1 12rem;
  }
  .controls :global(.ctl.narrow) {
    flex: 0 1 7rem;
  }
  label {
    color: var(--muted);
    font-size: 0.8rem;
  }
  select {
    background: var(--panel2);
    color: var(--ink);
    border: 1px solid var(--rule);
    border-radius: 7px;
    padding: 0.2rem 0.5rem;
    font: inherit;
    font-size: 0.82rem;
  }
  .muted {
    color: var(--muted);
  }
</style>
