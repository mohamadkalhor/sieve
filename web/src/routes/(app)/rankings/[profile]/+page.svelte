<script lang="ts">
  /** One profile's ranked list, with what carried each score and why #1 leads. */
  import { page } from '$app/stores';
  import { api, type ApiError } from '$lib/api/client';
  import type { Profile, Ranking } from '$lib/types';
  import AxisBars from '$lib/components/AxisBars.svelte';
  import ConfDots from '$lib/components/ConfDots.svelte';
  import Empty from '$lib/components/Empty.svelte';

  let ranking = $state<Ranking | null>(null);
  let profile = $state<Profile | null>(null);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);

  const name = $derived($page.params.profile ?? '');

  $effect(() => {
    const wanted = name;
    loading = true;
    error = null;
    Promise.all([api.ranking(wanted), api.profile(wanted)]).then(([r, p]) => {
      if (wanted !== name) return;
      ranking = r.ok ? r.value : null;
      profile = p.ok ? p.value : null;
      error = r.ok ? null : r.error;
      loading = false;
    });
  });

  const ranked = $derived((ranking?.ranks ?? []).filter((rank) => rank.position > 0));
  const setAside = $derived((ranking?.ranks ?? []).filter((rank) => rank.position === 0));
  const leader = $derived(ranked[0] ?? null);
</script>

<svelte:head><title>{name} · Rankings · Sieve</title></svelte:head>

<header class="top">
  <div>
    <h1>{name}</h1>
    {#if profile}<p class="lede">{profile.purpose}</p>{/if}
  </div>
  <a class="edit" href={`/profiles/${encodeURIComponent(name)}`}>Edit weights</a>
</header>

{#if loading}
  <p class="muted">Loading…</p>
{:else if ranked.length === 0}
  <Empty {error} title="Nothing ranks for this profile" hint="Pull a source, then reload." />
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
                <div class="flip">{rank.flip}</div>
              {/if}
            </td>
            <td><AxisBars axes={rank.axes ?? []} weights={profile?.weights ?? {}} /></td>
            <td class="right num">{rank.final.toFixed(3)}</td>
            <td><ConfDots confidence={rank.confidence} /></td>
            <td class="right num">
              {rank.cost_per_task == null ? '—' : `$${rank.cost_per_task.toPrecision(3)}`}
            </td>
            <td>
              <span class="dot" class:on={rank.reachable} title={rank.reachable ? 'reachable' : 'not reachable'}></span>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if leader}
    <p class="why">
      <strong class="mono">{leader.model_id}</strong> leads
      {#if ranked[1]}
        by {((leader.final - ranked[1].final) * 100).toFixed(1)} points over
        <span class="mono">{ranked[1].model_id}</span>.
      {:else}
        with nothing else eligible.
      {/if}
    </p>
  {/if}

  {#if setAside.length}
    <h2>Set aside</h2>
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
  {/if}
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
  .edit {
    border: 1px solid var(--rule);
    border-radius: 999px;
    padding: 0.25rem 0.8rem;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .edit:hover {
    color: var(--ink);
    border-color: var(--accent);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    min-width: 44rem;
  }
  thead th {
    position: sticky;
    top: 0;
    background: var(--bg);
    text-align: left;
    font-weight: 500;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--rule);
  }
  td {
    padding: 0.5rem;
    border-bottom: 1px solid var(--rule);
    vertical-align: middle;
  }
  tr.lead td {
    background: color-mix(in oklab, var(--accent) 7%, transparent);
  }
  tr.lead .id {
    color: var(--accent);
  }
  .pos {
    width: 2.2rem;
    color: var(--muted);
  }
  .right {
    text-align: right;
  }
  .id {
    overflow-wrap: anywhere;
  }
  .local,
  .flip {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .flip {
    font-family: var(--ui);
    color: var(--accent);
    opacity: 0.85;
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
  .why {
    color: var(--muted);
    margin-top: 0.9rem;
  }
  h2 {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-family: var(--ui);
    margin: 1.5rem 0 0.4rem;
  }
  .aside {
    list-style: none;
    margin: 0;
    padding: 0;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .aside li {
    display: flex;
    gap: 0.6rem;
    justify-content: space-between;
    padding: 0.25rem 0;
    border-bottom: 1px solid var(--rule);
    flex-wrap: wrap;
  }
</style>
