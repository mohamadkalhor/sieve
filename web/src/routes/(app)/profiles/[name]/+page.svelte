<script lang="ts">
  /**
   * The profile editor. Sliders re-rank locally on every input using the same
   * arithmetic the server uses, so the list moves as fast as the finger; the
   * server's `evaluate` is the answer of record when the user asks for it.
   */
  import { page } from '$app/stores';
  import { api, type ApiError } from '$lib/api/client';
  import type { Decision, Profile, Ranking } from '$lib/types';
  import Chip from '$lib/components/Chip.svelte';
  import ConfDots from '$lib/components/ConfDots.svelte';
  import Empty from '$lib/components/Empty.svelte';
  import WeightSlider from '$lib/components/WeightSlider.svelte';
  import { carriedBy, rankWithFloor, renormalise, weigh, type AxesByModel } from '$lib/rank/weigh';
  import { duration, reducedMotion } from '$lib/motion/reduced';
  import { flip } from 'svelte/animate';

  let profile = $state<Profile | null>(null);
  let ranking = $state<Ranking | null>(null);
  let weights = $state<Record<string, number>>({});
  let locked = $state<Set<string>>(new Set());
  let token = $state('');
  let error = $state<ApiError | null>(null);
  let notice = $state('');
  let serverDecision = $state<Decision | null>(null);
  let loading = $state(true);
  let busy = $state(false);

  const name = $derived($page.params.name ?? '');
  const dirty = $derived(
    profile ? JSON.stringify(sorted(weights)) !== JSON.stringify(sorted(profile.weights)) : false
  );

  function sorted(record: Record<string, number>) {
    return Object.fromEntries(
      Object.entries(record)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => [k, Number(v.toFixed(6))])
    );
  }

  $effect(() => {
    const wanted = name;
    loading = true;
    Promise.all([api.profile(wanted), api.ranking(wanted)]).then(([p, r]) => {
      if (wanted !== name) return;
      if (p.ok) {
        profile = p.value;
        weights = { ...p.value.weights };
      } else {
        error = p.error;
      }
      ranking = r.ok ? r.value : null;
      loading = false;
    });
  });

  /** axis values per model, straight from the ranking rows the server sent */
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

  const live = $derived(
    rankWithFloor(weigh(axesByModel, weights), profile?.policy?.min_confidence ?? 0)
  );
  const chainCut = $derived(profile?.policy?.chain ?? 5);

  const why = $derived.by(() => {
    if (live.length < 2 || !profile) return null;
    const [first, second] = live;
    const gap = (first.score - second.score) * 100;
    const axis = carriedBy(first.contributions, second.contributions);
    return {
      gap,
      clears: gap >= (profile.policy?.margin ?? 0),
      margin: profile.policy?.margin ?? 0,
      axis: axis?.axis ?? null,
      leader: first.model_id,
      runner: second.model_id
    };
  });

  function move(axis: string, value: number) {
    weights = renormalise(weights, axis, value, locked);
  }

  function toggleLock(axis: string, next: boolean) {
    const copy = new Set(locked);
    if (next) copy.add(axis);
    else copy.delete(axis);
    locked = copy;
  }

  function reset() {
    if (profile) weights = { ...profile.weights };
    notice = '';
    serverDecision = null;
  }

  async function evaluate() {
    busy = true;
    notice = '';
    const result = await api.evaluate(name, { token: token || undefined });
    busy = false;
    if (!result.ok) {
      error = result.error;
      notice = '';
      return;
    }
    error = null;
    serverDecision = result.value.decision;
    notice = 'Evaluated on the server. Nothing was stored.';
  }

  async function save() {
    busy = true;
    notice = '';
    const result = await api.setWeights(name, sorted(weights), { token: token || undefined });
    busy = false;
    if (!result.ok) {
      error = result.error;
      return;
    }
    error = null;
    profile = result.value;
    weights = { ...result.value.weights };
    notice = 'Saved. The YAML on disk changed and a decision was logged.';
    const again = await api.ranking(name);
    if (again.ok) ranking = again.value;
  }
</script>

<svelte:head><title>{name} · Profiles · Sieve</title></svelte:head>

<header class="top">
  <div>
    <h1>{name}</h1>
    {#if profile}<p class="lede">{profile.purpose}</p>{/if}
  </div>
  <a class="link" href={`/rankings/${encodeURIComponent(name)}`}>Full ranking</a>
</header>

{#if loading}
  <p class="muted">Loading…</p>
{:else if !profile}
  <Empty {error} title="No such profile" />
{:else}
  <div class="editor">
    <section class="left">
      <h2>Weights</h2>
      {#each Object.keys(weights).sort() as axis (axis)}
        <WeightSlider
          {axis}
          value={weights[axis]}
          locked={locked.has(axis)}
          onchange={(value) => move(axis, value)}
          onlock={(next) => toggleLock(axis, next)}
        />
      {/each}
      <p class="sum mono">
        sum {Object.values(weights).reduce((a, b) => a + b, 0).toFixed(3)}
      </p>

      <h2>Constraints</h2>
      <div class="chips">
        {#each Object.entries(profile.require ?? {}) as [key, value] (key)}
          <Chip label={key} value={typeof value === 'object' ? JSON.stringify(value) : String(value)} />
        {:else}
          <span class="muted">none</span>
        {/each}
      </div>

      <h2>Shape</h2>
      <div class="chips">
        {#each Object.entries(profile.shape ?? {}).filter(([, v]) => v != null) as [key, value] (key)}
          <Chip label={key.replace('_tokens', '')} value={String(value)} />
        {/each}
      </div>

      <h2>Policy</h2>
      <div class="chips">
        {#each Object.entries(profile.policy ?? {}) as [key, value] (key)}
          <Chip label={key} value={String(value)} tone={key === 'margin' ? 'accent' : 'muted'} />
        {/each}
      </div>

      <h2>Save</h2>
      <label class="token">
        <span>Token</span>
        <input
          type="password"
          bind:value={token}
          placeholder="a token with profiles:write"
          autocomplete="off"
        />
      </label>
      <div class="actions">
        <button type="button" onclick={evaluate} disabled={busy}>Evaluate</button>
        <button type="button" class="primary" onclick={save} disabled={busy || !dirty}>Save</button>
        <button type="button" onclick={reset} disabled={!dirty}>Reset</button>
      </div>
      {#if notice}<p class="notice">{notice}</p>{/if}
      {#if error}<p class="error">{error.message}</p>{/if}
      {#if serverDecision}
        <p class="decision mono">server: {serverDecision.reason}</p>
      {/if}
    </section>

    <section class="right">
      <h2>Live ranking {#if dirty}<span class="unsaved">unsaved</span>{/if}</h2>
      {#if why}
        <p class="gap">
          <span class="mono">{why.leader}</span> leads
          <span class="mono">{why.runner}</span> by {why.gap.toFixed(1)} points —
          {why.clears ? 'clears' : 'inside'} the margin of {why.margin.toFixed(1)}{#if why.axis},
            carried by {why.axis}{/if}.
        </p>
      {/if}
      <ol class="live">
        {#each live as row, index (row.model_id)}
          <li
            class:lead={index === 0}
            class:dim={index >= chainCut}
            animate:flip={{ duration: duration(240, $reducedMotion) }}
          >
            <span class="pos num">{index + 1}</span>
            <span class="id mono">{row.model_id}</span>
            <ConfDots confidence={row.confidence} />
            <span class="score num">{row.score.toFixed(3)}</span>
          </li>
        {:else}
          <li class="muted">Nothing to rank yet.</li>
        {/each}
      </ol>
    </section>
  </div>
{/if}

<style>
  .top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 0;
  }
  .link {
    border: 1px solid var(--rule);
    border-radius: 999px;
    padding: 0.25rem 0.8rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .editor {
    display: grid;
    grid-template-columns: minmax(0, 20rem) minmax(0, 1fr);
    gap: 1.5rem;
    align-items: start;
  }
  h2 {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-family: var(--ui);
    margin: 1.25rem 0 0.4rem;
  }
  h2:first-child {
    margin-top: 0;
  }
  .sum {
    color: var(--muted);
    font-size: 0.75rem;
    margin: 0.3rem 0 0;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.78rem;
    color: var(--muted);
  }
  .token input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
  }
  .actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.6rem;
    flex-wrap: wrap;
  }
  button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.82rem;
    padding: 0.3rem 0.8rem;
    cursor: pointer;
  }
  button.primary {
    border-color: var(--accent);
    color: var(--accent);
  }
  button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .notice {
    color: var(--good);
    font-size: 0.8rem;
  }
  .error {
    color: var(--bad);
    font-size: 0.8rem;
    overflow-wrap: anywhere;
  }
  .decision {
    color: var(--muted);
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }
  .unsaved {
    color: var(--accent);
    text-transform: none;
    letter-spacing: 0;
    margin-left: 0.4rem;
  }
  .gap {
    color: var(--muted);
    font-size: 0.82rem;
    margin: 0 0 0.6rem;
  }
  .live {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .live li {
    display: grid;
    grid-template-columns: 1.8rem minmax(0, 1fr) auto 3.5rem;
    gap: 0.6rem;
    align-items: center;
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--rule);
    transition: background 140ms ease;
  }
  .live li.lead {
    outline: 1px solid var(--accent);
    border-radius: 6px;
    background: color-mix(in oklab, var(--accent) 8%, transparent);
  }
  .live li.dim {
    opacity: 0.45;
  }
  .pos {
    color: var(--muted);
    font-size: 0.75rem;
  }
  .id {
    overflow-wrap: anywhere;
    font-size: 0.82rem;
  }
  .score {
    text-align: right;
    font-size: 0.82rem;
  }
  .muted {
    color: var(--muted);
  }
  @media (max-width: 900px) {
    .editor {
      grid-template-columns: 1fr;
    }
  }
</style>
