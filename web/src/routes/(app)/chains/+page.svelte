<script lang="ts">
  import { api, type ApiError } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import ChainCard from '$lib/components/ChainCard.svelte';
  import Empty from '$lib/components/Empty.svelte';

  let chains = $state<Chain[]>([]);
  let profiles = $state<Profile[]>([]);
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
      const results = await Promise.all(found.value.map((p) => api.chain(p.name)));
      chains = results.filter((r) => r.ok).map((r) => (r as { ok: true; value: Chain }).value);
      loading = false;
    })();
  });
</script>

<svelte:head><title>Chains · Sieve</title></svelte:head>

<h1>Chains</h1>
<p class="lede">What each profile ships: a primary and its fallbacks, as a target would receive them.</p>

{#if loading}
  <p class="muted">Loading…</p>
{:else if chains.length === 0}
  <Empty
    {error}
    title="No chains computed"
    hint={profiles.length
      ? 'Run `sieve plan --store` to compute and keep a chain for each profile.'
      : ''}
  />
{:else}
  <div class="grid">
    {#each chains as chain (chain.profile)}
      <ChainCard {chain} />
    {/each}
  </div>
{/if}

<style>
  h1 { font-size: 1.6rem; margin: 0; }
  .lede { color: var(--muted); margin: 0.25rem 0 1.25rem; max-width: 62ch; }
  .muted { color: var(--muted); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr)); gap: 0.7rem; }
</style>
