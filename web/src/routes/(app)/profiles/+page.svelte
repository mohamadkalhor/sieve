<script lang="ts">
  import { api, type ApiError } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import Empty from '$lib/components/Empty.svelte';

  let profiles = $state<Profile[]>([]);
  let chains = $state<Record<string, Chain>>({});
  let error = $state<ApiError | null>(null);
  let loading = $state(true);

  $effect(() => {
    (async () => {
      const found = await api.profiles();
      if (!found.ok) {
        error = found.error;
        loading = false;
        return;
      }
      profiles = found.value;
      const results = await Promise.all(
        found.value.map(async (p) => [p.name, await api.chain(p.name)] as const)
      );
      const held: Record<string, Chain> = {};
      for (const [name, result] of results) if (result.ok) held[name] = result.value;
      chains = held;
      loading = false;
    })();
  });

  const byModality = $derived.by(() => {
    const grouped = new Map<string, Profile[]>();
    for (const profile of profiles) {
      grouped.set(profile.modality, [...(grouped.get(profile.modality) ?? []), profile]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  });
</script>

<svelte:head><title>Profiles · Sieve</title></svelte:head>

<h1>Profiles</h1>
<p class="lede">
  One per role your agents play. Open one to move its weights and watch the list re-rank.
</p>

{#if loading}
  <p class="muted">Loading…</p>
{:else if profiles.length === 0}
  <Empty {error} title="No profiles" hint="Add a YAML file under profiles/&lt;modality&gt;/." />
{/if}

{#each byModality as [modality, group] (modality)}
  <section>
    <h2>{modality}</h2>
    <div class="grid">
      {#each group as profile (profile.name)}
        {@const total = Object.values(profile.weights).reduce((a, b) => a + b, 0) || 1}
        <a class="card" href={`/profiles/${encodeURIComponent(profile.name)}`}>
          <div class="name">{profile.name}</div>
          <div class="purpose">{profile.purpose}</div>
          <div class="mini" role="img" aria-label="weight vector">
            {#each Object.entries(profile.weights).sort( ([a], [b]) => a.localeCompare(b) ) as [axis, weight] (axis)}
              <span
                class="seg"
                style:flex={weight / total}
                title={`${axis} ${weight.toFixed(2)}`}
              ></span>
            {/each}
          </div>
          <div class="primary mono">{chains[profile.name]?.primary ?? 'no chain yet'}</div>
        </a>
      {/each}
    </div>
  </section>
{/each}

<style>
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 1.25rem;
    max-width: 62ch;
  }
  .muted {
    color: var(--muted);
  }
  h2 {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-family: var(--ui);
    margin: 1.25rem 0 0.5rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 0.6rem;
  }
  .card {
    display: block;
    padding: 0.8rem 0.9rem;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
  }
  .card:hover {
    border-color: var(--accent);
  }
  .name {
    font-family: var(--display);
    font-size: 1.05rem;
  }
  .purpose {
    color: var(--muted);
    font-size: 0.78rem;
    min-height: 2.4em;
  }
  .mini {
    display: flex;
    gap: 1px;
    height: 5px;
    margin: 0.5rem 0 0.4rem;
  }
  .seg {
    background: var(--reach);
    border-radius: 1px;
  }
  .seg:first-child {
    background: var(--accent);
  }
  .primary {
    color: var(--muted);
    font-size: 0.72rem;
    overflow-wrap: anywhere;
  }
</style>
