<script lang="ts">
  /** Field: every measured model at a glance, one axis against what it costs. */
  import { goto } from '$app/navigation';
  import { api, type ApiError, type ModelRow } from '$lib/api/client';
  import type {
    Axis,
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
    rawPerMillion,
    sameRateShare
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

  /** the two searches, and the x axis they are read against */
  let provider = $state('');
  let model = $state('');
  /**
   * Which cost axis, and how much of a preset it owes its number to.
   *
   * `per_task` costs one named profile's declared shape and `per_million`
   * blends input against output at a ratio this app picked. Both are derived.
   * `input` and `output` are the posted numbers themselves — the axes to reach
   * for when the question is what a model costs rather than what it costs
   * *here*.
   */
  type CostAxis = 'per_task' | 'per_million' | 'input' | 'output';
  let costAxis = $state<CostAxis>('per_task');
  /** whose task the cost axis is costing -- see `costs` below */
  let shape = $state('');

  const DEFAULT_AXIS: Record<string, string> = { llm: 'intelligence' };

  /** Only axes at least one profile actually weights: the rest are noise here. */
  const usedAxes = $derived.by(() => {
    const weighted = new Set(profiles.flatMap((p) => Object.keys(p.weights)));
    return axes.filter((axis) => weighted.has(axis.name));
  });

  const primaries = $derived(
    new Set(
      Object.values(rankings)
        .map((ranking) => ranking.ranks?.find((rank) => rank.position === 1)?.model_id)
        .filter((id): id is string => Boolean(id))
    )
  );

  /**
   * Cost per task, from **one named profile's** ranking.
   *
   * There is no such thing as the cost of a task. `reader` sends 200k input
   * tokens and `cheap_bulk` sends 2k, so the same model differs by two orders
   * of magnitude between them, and the effort question changes answer with it:
   * at `reader`'s shape the input swamps everything and every mode costs
   * within 6% of every other, while at `cheap_bulk`'s the modes spread over 5x.
   *
   * This used to merge every profile's ranking and keep whichever arrived
   * first, which made the axis depend on the order nine fetches happened to
   * resolve in — the same screen, reloaded, drew different numbers. So it names
   * one, and the name is on screen beside the axis.
   */
  const costs = $derived.by(() => {
    const out = new Map<string, number>();
    for (const rank of rankings[shape]?.ranks ?? []) {
      if (rank.cost_per_task != null) out.set(rank.model_id, rank.cost_per_task);
    }
    return out;
  });

  /**
   * Whether that cost came from the model's own traffic or from the profile's
   * declared shape.
   *
   * This is the whole point of the cost-per-task axis. Effort does not change
   * the rate, it changes how many tokens come back — so a cost built from a
   * shape is the *same* number for every mode of a family and its line is
   * vertical for a second, duller reason. Only telemetry moves it, and the
   * chart has to say which points those are.
   */
  const costFrom = $derived.by(() => {
    const out = new Map<string, 'shape' | 'telemetry'>();
    for (const rank of rankings[shape]?.ranks ?? []) {
      if (rank.cost_from) out.set(rank.model_id, rank.cost_from);
    }
    return out;
  });

  const values = $derived.by(() => {
    const out = new Map<string, number>();
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

  /** Only `per_task` is built from telemetry; every other axis is a price list. */
  const fromPriceList = $derived(costAxis !== 'per_task');

  function costOf(row: ModelRow): number | null {
    if (costAxis === 'per_task') return perTask(row);
    if (costAxis === 'per_million') return postedPerMillion(row.price);
    return rawPerMillion(row.price, costAxis);
  }

  const points = $derived<Point[]>(
    models.map((row) => ({
      id: row.id,
      x: costOf(row),
      y: values.get(row.id) ?? null,
      reachable: row.reachable,
      primary: primaries.has(row.id),
      effort: row.effort,
      lit: marked.lit.has(row.id),
      dim: searching && !marked.lit.has(row.id),
      hidden: marked.drawn !== null && !marked.drawn.has(row.id),
      // on the price list itself there is nothing to measure, so no claim
      measured: fromPriceList ? undefined : costFrom.get(row.id) === 'telemetry'
    }))
  );

  const lines = $derived<Line[]>(
    [...marked.lines].map(([family, modes]) => ({ family, ids: modes.map((m) => m.id) }))
  );

  function perTask(row: ModelRow): number | null {
    return costs.get(row.id) ?? fallbackCost(row);
  }

  function fallbackCost(row: ModelRow): number | null {
    const price = row.price;
    if (!price) return null;
    if (price.input != null || price.output != null) {
      // a nominal 10k in / 1k out, only so an unranked model still has a place
      return ((price.input ?? 0) * 10_000 + (price.output ?? 0) * 1_000) / 1_000_000 || null;
    }
    return price.per_unit ?? null;
  }

  async function load(which: Modality) {
    loading = true;
    error = null;

    /*
      The board is fetched *beside* this, not inside it.

      `/v1/leaderboard?modality=llm` takes about ten seconds to compute, and it
      used to sit in the same `Promise.all` as the models — so the whole screen,
      chart included, waited on a ranking that is drawn underneath the chart or
      not at all. Ten seconds of "Loading…" is indistinguishable from a broken
      page. Now the scatter paints as soon as its own data lands and the board
      arrives when it arrives.

      A board is slow enough that the tab can change while it is in flight, and
      one for the modality you just left must not replace the one you are
      looking at — so the answer is checked against the tab still on screen.
      That comparison is the guard rather than a request counter: a counter
      would have to be read and written in the same breath here, and this
      function runs inside an effect, where reading what you just wrote is how
      you get an infinite loop.
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
    axisName = wanted.includes(axisName) ? axisName : (DEFAULT_AXIS[which] ?? wanted[0] ?? '');

    const loaded: Record<string, Ranking> = {};
    await Promise.all(
      profiles.map(async (profile) => {
        const found = await api.ranking(profile.name);
        if (found.ok) loaded[profile.name] = found.value;
      })
    );
    rankings = loaded;
    // alphabetical, so the axis is the same on every reload; changeable, because
    // which task you are costing is a real question and not ours to answer
    const named = profiles.map((p) => p.name).sort();
    shape = named.includes(shape) ? shape : (named[0] ?? '');
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
    // a modality change resets the metric: `elo:with_vocals` means nothing
    // outside music. It resets the search for the same reason.
    void modality;
    metric = undefined;
    provider = '';
    model = '';
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
   * knows how many models carry a price. Today only `llm` clears it: on these
   * recordings 5 of 313 scored media models have one.
   */
  const showScatter = $derived(board?.scatter_ok !== false);

  const matched = $derived(models.filter((m) => m.reachable).length);

  /**
   * How many multi-mode families charge one rate for every mode.
   *
   * Counted on the data actually loaded, so the sentence is never stale and
   * never someone else's dataset.
   */
  const rates = $derived(sameRateShare(models));
  const shaped = $derived(profiles.find((p) => p.name === shape));

  /**
   * What one task of this profile is, in its own units.
   *
   * A `Shape` only fills the fields its modality uses, so an llm profile has
   * tokens and a video profile has seconds. Printing "0 tokens in, 0 out" under
   * a video scatter -- which is what this said until media had prices to plot
   * against -- describes nothing and looks like a bug in the data.
   */
  const shapeWords = $derived.by(() => {
    const s = shaped?.shape;
    if (!s) return 'no declared shape';
    const parts: string[] = [];
    if (s.in_tokens) parts.push(`${s.in_tokens.toLocaleString()} tokens in`);
    if (s.out_tokens) parts.push(`${s.out_tokens.toLocaleString()} out`);
    if (s.seconds) parts.push(`${s.seconds}s of output`);
    if (s.images) parts.push(`${s.images} image${s.images === 1 ? '' : 's'}`);
    if (s.chars) parts.push(`${s.chars.toLocaleString()} characters`);
    if (s.megapixels) parts.push(`${s.megapixels} megapixels`);
    if (s.requests) parts.push(`${s.requests} request${s.requests === 1 ? '' : 's'}`);
    return parts.length ? parts.join(', ') : 'no declared shape';
  });
  /** Do the posted prices here even have two sides to separate? */
  const sided = $derived(hasSidedPrices(models));

  const X_LABEL: Record<CostAxis, string> = {
    per_task: 'cost per task (USD)',
    per_million: 'posted price per 1M tokens, blended (USD)',
    input: 'posted input price per 1M tokens (USD)',
    output: 'posted output price per 1M tokens (USD)'
  };
  const xLabel = $derived(X_LABEL[costAxis]);

  /**
   * A modality with no sided prices cannot offer them, and a stale choice has
   * to fall back rather than silently plot nothing.
   */
  $effect(() => {
    if (!sided && (costAxis === 'input' || costAxis === 'output')) costAxis = 'per_million';
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
    One row, four controls. They used to be three stacked rows -- axis, then the
    two search boxes, then the cost axis -- which pushed the chart itself below
    the fold on a laptop. They are one question ("what am I looking at, against
    what") so they are one line, and the prose that explains the choice sits
    under the row instead of between the controls, where it was doing the
    stacking.
  -->
  <div class="controls">
    <label class="ctl">
      <span>Axis</span>
      <select id="axis" bind:value={axisName}>
        {#each usedAxes as axis (axis.name)}
          <option value={axis.name}>{axis.label || axis.name}</option>
        {/each}
      </select>
    </label>

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

    <label class="ctl wide">
      <span>Cost axis</span>
      <select id="cost-axis" bind:value={costAxis}>
        <option value="per_task">cost per task</option>
        <option value="per_million">price per 1M, blended</option>
        {#if sided}
          <option value="input">input price per 1M</option>
          <option value="output">output price per 1M</option>
        {/if}
      </select>
    </label>

    {#if costAxis === 'per_task' && profiles.length > 1}
      <label class="ctl narrow">
        <span>of</span>
        <select id="shape" bind:value={shape}>
          {#each [...profiles].sort( (a, b) => a.name.localeCompare(b.name) ) as p (p.name)}
            <option value={p.name}>{p.name}</option>
          {/each}
        </select>
      </label>
    {/if}
  </div>

  <p class="describes">
    {#if axisName}
      <span class="what">{usedAxes.find((a) => a.name === axisName)?.describes ?? ''}</span>
    {/if}
    {#if costAxis === 'per_million'}
      Cost is what the provider charges, input and output blended {BLEND_IN / BLEND_OUT} : 1.
    {:else if costAxis === 'input' || costAxis === 'output'}
      Cost is the posted {costAxis} rate exactly as published — no blend, no profile.
    {:else}
      Cost is one {shape} task — {shapeWords} — measured from the model's own traffic where
      there is any, a posted price where there is not.
    {/if}
    {#if lines.length > 0 && costAxis === 'per_task'}
      <!--
        The shapes need naming somewhere, and this is where the reader is
        already looking. The chart's own legend is hidden while a line is drawn,
        because it names colours that are not on screen and sits exactly where
        the top mode labels land.
      -->
      <span class="shapes mono">
        <i class="dot"></i> measured
        <i class="box"></i> posted price
      </span>
    {/if}
  </p>

  {#if rates.total > 0}
    <p class="rates" role="note">
      Of the {rates.total} families here that publish more than one effort mode,
      <strong>{rates.same}</strong> charge one rate for every mode: effort changes how many
      tokens come back, not the rate. So on <em>price per million tokens</em> their lines are
      vertical — the price list, not a fault in the chart.
    </p>
  {/if}
{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if showScatter && points.some((p) => p.y !== null)}
  <Scatter
    {points}
    {lines}
    {xLabel}
    yLabel={axisName}
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
  .describes {
    color: var(--muted);
    font-size: 0.78rem;
    line-height: 1.5;
    max-width: 88ch;
    margin: 0 0 0.7rem;
  }
  .describes .what::after {
    content: ' ·';
    opacity: 0.5;
  }
  .shapes {
    color: var(--muted);
    font-size: 0.72rem;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }
  .shapes .dot,
  .shapes .box {
    display: inline-block;
    width: 7px;
    height: 7px;
    background: var(--good);
  }
  .shapes .dot {
    border-radius: 50%;
  }
  .shapes .box {
    background: transparent;
    border: 1.5px solid var(--good);
    margin-left: 0.5rem;
  }
  .rates {
    color: var(--muted);
    font-size: 0.78rem;
    line-height: 1.55;
    max-width: 72ch;
    margin: 0 0 0.9rem;
  }
  .rates strong {
    color: var(--ink);
    font-weight: 600;
  }
  .muted {
    color: var(--muted);
  }
</style>
