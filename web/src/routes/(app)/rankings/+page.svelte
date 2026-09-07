<script lang="ts">
  import { api } from '$lib/api/client';
  import type { Profile } from '$lib/types';
  import Empty from '$lib/components/Empty.svelte';

  let profiles = $state<Profile[]>([]);
  let error = $state<import('$lib/api/client').ApiError | null>(null);

  $effect(() => {
    api.profiles().then((result) => {
      if (result.ok) profiles = result.value;
      else error = result.error;
    });
  });

  const byModality = $derived.by(() => {
    const grouped = new Map<string, Profile[]>();
    for (const profile of profiles) {
      grouped.set(profile.modality, [...(grouped.get(profile.modality) ?? []), profile]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  });
</script>

<svelte:head><title>Rankings · Sieve</title></svelte:head>

<h1>Rankings</h1>
<p class="lede">Pick a profile to see its ranked list, with what carried each score.</p>

{#if profiles.length === 0}
  <Empty {error} title="No profiles" hint="Add a YAML file under profiles/&lt;modality&gt;/." />
{/if}

{#each byModality as [modality, group] (modality)}
  <section>
    <h2>{modality}</h2>
    <ul>
      {#each group as profile (profile.name)}
        <li>
          <a href={`/rankings/${encodeURIComponent(profile.name)}`}>
            <span class="name">{profile.name}</span>
            <span class="purpose">{profile.purpose}</span>
          </a>
        </li>
      {/each}
    </ul>
  </section>
{/each}

<style>
  h1 { font-size: 1.6rem; margin: 0; }
  .lede { color: var(--muted); margin: 0.25rem 0 1.25rem; }
  h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin: 1.25rem 0 0.5rem; font-family: var(--ui); }
  ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 0.5rem; }
  li a { display: block; padding: 0.7rem 0.85rem; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--panel); }
  li a:hover { border-color: var(--accent); }
  .name { display: block; font-family: var(--display); font-size: 1rem; }
  .purpose { color: var(--muted); font-size: 0.78rem; }
</style>
