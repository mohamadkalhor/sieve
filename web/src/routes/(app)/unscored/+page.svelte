<script lang="ts">
  /**
   * Unscored: the models your routers serve that no source has benchmarked.
   *
   * Without scores a model ranks at zero, which reads as "bad" when the truth
   * is "unknown". Each one can be scored here by hand, axis by axis, 0 to 100;
   * a hand score becomes that axis's value for the model and it ranks on it
   * from the next preview. An agent does the same through the API:
   *
   *   GET /v1/unscored                      the list, with any hand scores
   *   PUT /v1/hand-scores                   {local_id, modality, scores, name?}
   *
   * A router id that matches a model the catalogue already knows is better
   * linked than scored; that is the Sources screen.
   */
  import {
    MODALITY_OPTIONS,
    api,
    explainError,
    type AxisRow,
    type UnscoredRow
  } from '$lib/api/client';
  import type { Modality } from '$lib/types';
  import { session } from '$lib/session.svelte';

  type Filter = 'all' | 'no_match' | 'no_scores' | 'hand';

  let rows = $state<UnscoredRow[]>([]);
  let loading = $state(true);
  let failed = $state<string | null>(null);
  let filter = $state<Filter>('all');
  let search = $state('');

  /** the row being scored, and its draft */
  let open = $state<string | null>(null);
  let modality = $state<Modality>('llm');
  let name = $state('');
  let draft = $state<Record<string, string>>({});
  let axes = $state<AxisRow[]>([]);
  let saving = $state(false);
  let said = $state<{ ok: boolean; text: string } | null>(null);

  const options = $derived({ token: session.token || undefined });

  async function load(): Promise<void> {
    const result = await api.unscored(options);
    loading = false;
    if (!result.ok) {
      failed = explainError(result.error);
      return;
    }
    failed = null;
    rows = result.value ?? [];
  }

  $effect(() => {
    void load();
  });

  const counts = $derived({
    all: rows.length,
    no_match: rows.filter((r) => r.reason === 'no_match').length,
    no_scores: rows.filter((r) => r.reason === 'no_scores' && !scored(r)).length,
    hand: rows.filter(scored).length
  });

  const shown = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return rows
      .filter((r) =>
        filter === 'all'
          ? true
          : filter === 'hand'
            ? scored(r)
            : filter === 'no_scores'
              ? r.reason === 'no_scores' && !scored(r)
              : r.reason === filter
      )
      .filter(
        (r) =>
          !needle ||
          r.local_id.toLowerCase().includes(needle) ||
          r.name.toLowerCase().includes(needle)
      );
  });

  function scored(row: UnscoredRow): boolean {
    return Object.keys(row.hand ?? {}).length > 0;
  }

  function key(row: UnscoredRow): string {
    return `${row.local_id}|${row.modality ?? ''}`;
  }

  async function loadAxes(which: Modality): Promise<void> {
    const result = await api.axes(which);
    axes = result.ok && Array.isArray(result.value) ? result.value : [];
  }

  async function begin(row: UnscoredRow): Promise<void> {
    if (open === key(row)) {
      open = null;
      return;
    }
    open = key(row);
    said = null;
    modality = row.modality ?? 'llm';
    name = row.reason === 'no_match' ? prettify(row.name) : row.name;
    draft = Object.fromEntries(
      Object.entries(row.hand ?? {}).map(([axis, value]) => [axis, String(Math.round(value * 100))])
    );
    await loadAxes(modality);
  }

  async function changeModality(which: Modality): Promise<void> {
    modality = which;
    draft = {};
    await loadAxes(which);
  }

  function prettify(slug: string): string {
    return slug
      .split(/[-_]/)
      .filter(Boolean)
      .map((part) => (/^\d/.test(part) ? part : part[0].toUpperCase() + part.slice(1)))
      .join(' ');
  }

  async function save(row: UnscoredRow, clear = false): Promise<void> {
    const scores: Record<string, number | null> = {};
    for (const axis of axes) {
      const raw = (draft[axis.name] ?? '').trim();
      const held = row.hand?.[axis.name] !== undefined;
      if (clear) {
        if (held) scores[axis.name] = null;
        continue;
      }
      if (raw === '') {
        if (held) scores[axis.name] = null;
        continue;
      }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0 || value > 100) {
        said = { ok: false, text: `${axis.label || axis.name}: a score is 0 to 100.` };
        return;
      }
      scores[axis.name] = value / 100;
    }
    if (!Object.keys(scores).length) {
      said = { ok: false, text: clear ? 'Nothing to clear.' : 'Give at least one axis a score.' };
      return;
    }
    saving = true;
    const result = await api.saveHandScores(
      { local_id: row.local_id, modality, scores, name: name.trim() || undefined },
      options
    );
    saving = false;
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    said = {
      ok: true,
      text: clear ? 'Cleared.' : 'Saved. Profiles rank it on these scores from their next preview.'
    };
    if (clear) draft = {};
    await load();
  }

  const FILTERS: { id: Filter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'no_match', label: 'Unknown name' },
    { id: 'no_scores', label: 'No benchmarks' },
    { id: 'hand', label: 'Scored by hand' }
  ];
</script>

<svelte:head><title>Unscored · Sieve</title></svelte:head>

<header class="head">
  <h1>Unscored models</h1>
  <p class="lede">
    Your routers serve these, but no source has benchmarked them, so they rank at zero. Score one by
    hand and every profile ranks it on those scores. A model the catalogue already knows under
    another name is better <a href="/sources">linked on Sources</a>. Agents can do the same with
    <code>GET /v1/unscored</code> and <code>PUT /v1/hand-scores</code>.
  </p>
</header>

<div class="bar">
  <div class="filters" role="tablist" aria-label="Filter">
    {#each FILTERS as item (item.id)}
      <button
        type="button"
        role="tab"
        aria-selected={filter === item.id}
        class:on={filter === item.id}
        onclick={() => (filter = item.id)}
        >{item.label} <span class="mono">{counts[item.id]}</span></button
      >
    {/each}
  </div>
  <input
    class="search"
    type="search"
    placeholder="Search…"
    bind:value={search}
    spellcheck="false"
    autocomplete="off"
  />
</div>

{#if loading}
  <p class="muted">Loading…</p>
{:else if failed}
  <p class="bad">{failed}</p>
{:else if rows.length === 0}
  <p class="muted">Every model your routers serve has benchmark scores.</p>
{:else}
  <ul class="list">
    {#each shown as row (key(row))}
      {@const isOpen = open === key(row)}
      <li class="item" class:isOpen>
        <button type="button" class="summary" aria-expanded={isOpen} onclick={() => begin(row)}>
          <span class="who">
            <span class="name">{row.name}</span>
            <span class="mono id">{row.local_id}</span>
          </span>
          <span class="tags">
            {#if scored(row)}
              <span class="tag hand">{Object.keys(row.hand).length} scored by hand</span>
            {:else if row.reason === 'no_match'}
              <span class="tag">unknown name</span>
            {:else}
              <span class="tag">no benchmarks</span>
            {/if}
            {#if row.modality}<span class="tag quiet">{row.modality}</span>{/if}
          </span>
          <span class="action">{isOpen ? 'Close' : scored(row) ? 'Edit' : 'Score'}</span>
        </button>

        {#if isOpen}
          <div class="editor">
            <div class="meta">
              {#if row.reason === 'no_match'}
                <label>
                  <span>Name</span>
                  <input bind:value={name} spellcheck="false" autocomplete="off" />
                </label>
                <label>
                  <span>Kind</span>
                  <select
                    value={modality}
                    onchange={(event) => changeModality(event.currentTarget.value as Modality)}
                  >
                    {#each MODALITY_OPTIONS as option (option)}
                      <option value={option}>{option}</option>
                    {/each}
                  </select>
                </label>
              {:else}
                <p class="sub">
                  Matched to <span class="mono">{row.model_id}</span>, which no source has measured.
                </p>
              {/if}
            </div>

            <p class="sub">0 to 100 per axis, where 100 is as good as the best model. Leave blank to skip.</p>
            <div class="grid">
              {#each axes as axis (axis.name)}
                <label class="score">
                  <span class="axis">
                    <span class="axis-name">{axis.label || axis.name}</span>
                    <span class="axis-meaning">{axis.meaning || axis.describes}</span>
                  </span>
                  <input
                    class="mono"
                    type="number"
                    min="0"
                    max="100"
                    step="1"
                    placeholder="—"
                    aria-label={`${axis.label || axis.name}, 0 to 100`}
                    bind:value={draft[axis.name]}
                  />
                </label>
              {:else}
                <p class="sub">No axes for {modality}.</p>
              {/each}
            </div>

            <div class="acts">
              <button type="button" class="primary" disabled={saving} onclick={() => save(row)}
                >{saving ? 'Saving…' : 'Save scores'}</button
              >
              {#if scored(row)}
                <button type="button" class="quiet" disabled={saving} onclick={() => save(row, true)}
                  >Clear all</button
                >
              {/if}
              {#if said}<span class:bad={!said.ok} class="said">{said.text}</span>{/if}
            </div>
          </div>
        {/if}
      </li>
    {:else}
      <li class="muted empty">Nothing matches.</li>
    {/each}
  </ul>
{/if}

<style>
  .muted {
    color: var(--muted);
  }
  .bad {
    color: var(--bad);
  }
  .mono {
    font-family: var(--mono);
  }
  .head h1 {
    margin: 0;
    font-size: 1.6rem;
  }
  .lede {
    color: var(--muted);
    margin: 0.35rem 0 1.1rem;
    max-width: 72ch;
    font-size: 14px;
    line-height: 1.5;
  }
  .lede a {
    color: var(--reach);
  }
  .lede code {
    font-size: 12px;
  }

  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .filters button {
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--panel2);
    color: var(--muted);
    font: inherit;
    font-size: 13px;
    padding: 5px 12px;
    cursor: pointer;
  }
  .filters button span {
    font-size: 11px;
    margin-left: 4px;
  }
  .filters button.on {
    border-color: var(--accent);
    color: var(--ink);
  }
  .search,
  .meta input,
  .meta select {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 7px 10px;
  }
  .search {
    min-width: 0;
    width: 16rem;
    max-width: 100%;
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: hidden;
  }
  .item + .item {
    border-top: 1px solid var(--rule);
  }
  .empty {
    padding: 14px 16px;
  }
  .summary {
    width: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 4rem;
    align-items: center;
    gap: 14px;
    background: none;
    border: none;
    color: var(--ink);
    font: inherit;
    text-align: left;
    padding: 12px 16px;
    cursor: pointer;
  }
  .summary:hover,
  .isOpen .summary {
    background: var(--panel2);
  }
  .summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .who {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .name {
    font-size: 15px;
    font-weight: 600;
  }
  .id {
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: flex-end;
  }
  .tag {
    font-size: 11px;
    border: 1px solid var(--rule);
    border-radius: 999px;
    padding: 2px 8px;
    color: var(--warn);
  }
  .tag.hand {
    color: var(--good);
  }
  .tag.quiet {
    color: var(--muted);
  }
  .action {
    font-size: 13px;
    color: var(--reach);
    text-align: right;
  }

  .editor {
    padding: 4px 16px 16px;
    background: var(--panel2);
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 8px;
  }
  .meta label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: var(--muted);
  }
  .meta input {
    width: 18rem;
    max-width: 100%;
  }
  .sub {
    font-size: 12px;
    color: var(--muted);
    margin: 4px 0 8px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
    gap: 8px;
  }
  .score {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 4.5rem;
    align-items: center;
    gap: 10px;
    border: 1px solid var(--rule);
    border-radius: 8px;
    background: var(--panel);
    padding: 8px 10px;
  }
  .axis {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .axis-name {
    font-size: 13px;
    font-weight: 600;
  }
  .axis-meaning {
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .score input {
    width: 100%;
    box-sizing: border-box;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font-size: 14px;
    padding: 5px 7px;
    text-align: right;
  }
  .score input:focus {
    border-color: var(--accent);
    outline: none;
  }
  .acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    margin-top: 12px;
  }
  .primary {
    background: var(--accent);
    color: var(--bg);
    border: none;
    border-radius: 7px;
    padding: 8px 16px;
    font: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  .primary:disabled,
  .quiet:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .quiet {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel);
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 7px 12px;
    cursor: pointer;
  }
  .said {
    font-size: 13px;
    color: var(--good);
  }
  .said.bad {
    color: var(--bad);
  }

  @media (max-width: 700px) {
    .summary {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .tags {
      grid-column: 1 / -1;
      grid-row: 2;
      justify-content: flex-start;
    }
    .search {
      width: 100%;
    }
  }
</style>
