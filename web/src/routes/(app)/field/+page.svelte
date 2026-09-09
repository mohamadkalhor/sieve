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
  import { BLEND_IN, BLEND_OUT, mark, postedPerMillion, sameRateShare } from '$lib/field';
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
  let costAxis = $state<'per_task' | 'per_million'>('per_task');
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

  const points = $derived<Point[]>(
    models.map((row) => ({
      id: row.id,
      x: costAxis === 'per_million' ? postedPerMillion(row.price) : perTask(row),
      y: values.get(row.id) ?? null,
      reachable: row.reachable,
      primary: primaries.has(row.id),
      effort: row.effort,
      lit: marked.lit.has(row.id),
      dim: searching && !marked.lit.has(row.id),
      hidden: marked.drawn !== null && !marked.drawn.has(row.id),
      // on the price list itself there is nothing to measure, so no claim
      measured:
        costAxis === 'per_million' ? undefined : costFrom.get(row.id) === 'telemetry'
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
    const [axesResult, profilesResult, modelsResult, boardResult] = await Promise.all([
      api.axes(which),
      api.profiles(which),
      api.models({ modality: which, limit: 1000 }),
      api.leaderboard(which, metric)
    ]);
    board = boardResult.ok ? boardResult.value : null;

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
    metric = next;
    const again = await api.leaderboard(modality, next);
    if (again.ok) board = again.value;
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
  const xLabel = $derived(
    costAxis === 'per_million' ? 'posted price per 1M tokens (USD)' : 'cost per task (USD)'
  );
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

<div class="picker" hidden={!showScatter}>
  <label for="axis">Axis</label>
  <select id="axis" bind:value={axisName}>
    {#each usedAxes as axis (axis.name)}
      <option value={axis.name}>{axis.label || axis.name}</option>
    {/each}
  </select>
  {#if axisName}
    <span class="describes">{usedAxes.find((a) => a.name === axisName)?.describes ?? ''}</span>
  {/if}
</div>

{#if showScatter}
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

  <div class="picker cost">
    <label for="cost-axis">Cost axis</label>
    <select id="cost-axis" bind:value={costAxis}>
      <option value="per_task">cost per task</option>
      <option value="per_million">price per million tokens</option>
    </select>
    {#if costAxis === 'per_task' && profiles.length > 1}
      <label for="shape">of</label>
      <select id="shape" bind:value={shape}>
        {#each [...profiles].sort( (a, b) => a.name.localeCompare(b.name) ) as p (p.name)}
          <option value={p.name}>{p.name}</option>
        {/each}
      </select>
    {/if}
    <span class="describes">
      {#if costAxis === 'per_million'}
        What the provider charges, input and output blended {BLEND_IN / BLEND_OUT} : 1.
      {:else}
        One {shape} task — {shapeWords} — measured from the model's own traffic where there
        is any, a posted price where there is not.
      {/if}
    </span>
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
  </div>

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
  .picker {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.7rem;
    flex-wrap: wrap;
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
    max-width: 62ch;
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
