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
  import Empty from '$lib/components/Empty.svelte';
  import Kpi from '$lib/components/Kpi.svelte';
  import Leaderboard from '$lib/components/Leaderboard.svelte';
  import Scatter, { type Point } from '$lib/components/Scatter.svelte';

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

  /** cost per task, taken from the ranking that used this profile's shape. */
  const costs = $derived.by(() => {
    const out = new Map<string, number>();
    for (const ranking of Object.values(rankings)) {
      for (const rank of ranking.ranks ?? []) {
        if (rank.cost_per_task != null && !out.has(rank.model_id)) {
          out.set(rank.model_id, rank.cost_per_task);
        }
      }
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

  const points = $derived<Point[]>(
    models.map((model) => ({
      id: model.id,
      x: costs.get(model.id) ?? fallbackCost(model),
      y: values.get(model.id) ?? null,
      reachable: model.reachable,
      primary: primaries.has(model.id)
    }))
  );

  function fallbackCost(model: ModelRow): number | null {
    const price = model.price;
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
    // outside music
    void modality;
    metric = undefined;
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
   * knows how many models carry a price. Today only `llm` clears it: 5 of 313
   * scored media models have one.
   */
  const showScatter = $derived(board?.scatter_ok !== false);

  const matched = $derived(models.filter((m) => m.reachable).length);
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

{#if loading}
  <p class="muted">Loading…</p>
{:else if showScatter && points.some((p) => p.y !== null)}
  <Scatter
    {points}
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
  }
  .muted {
    color: var(--muted);
  }
</style>
