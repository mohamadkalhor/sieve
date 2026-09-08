<script lang="ts">
  import { api, type ApiError } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import Empty from '$lib/components/Empty.svelte';

  let profiles = $state<Profile[]>([]);
  let chains = $state<Record<string, Chain>>({});
  let error = $state<ApiError | null>(null);
  let loading = $state(true);

  // New profile, cloned. Starting from nothing means assembling weights that
  // sum to 1 over axes that exist for a modality you have not chosen yet;
  // starting from the seat next to it and changing two numbers is how anybody
  // actually makes one.
  let cloning = $state(false);
  let cloneFrom = $state('');
  let cloneName = $state('');
  let clonePurpose = $state('');
  let token = $state('');
  let busy = $state(false);

  async function clone(event: SubmitEvent) {
    event.preventDefault();
    busy = true;
    const result = await api.createProfile(
      { name: cloneName.trim(), from: cloneFrom, purpose: clonePurpose.trim() || undefined },
      { token: token || undefined }
    );
    busy = false;
    if (!result.ok) {
      error = result.error;
      return;
    }
    error = null;
    window.location.href = `/profiles/${encodeURIComponent(result.value.name)}`;
  }

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

<header class="top">
  <div>
    <h1>Profiles</h1>
    <p class="lede">
      One per role your agents play. Open one to move its weights and watch the list re-rank.
    </p>
  </div>
  <button type="button" class="new" onclick={() => (cloning = !cloning)} aria-expanded={cloning}>
    {cloning ? 'Cancel' : 'New profile'}
  </button>
</header>

{#if cloning}
  <form class="clone" onsubmit={clone}>
    <p class="hint">
      Cloned from an existing seat: weights, constraints, shape and policy come across, and you
      change what differs.
    </p>
    <div class="fields">
      <label>
        <span>Clone from</span>
        <select bind:value={cloneFrom} required>
          <option value="" disabled>choose a profile</option>
          {#each profiles as p (p.name)}
            <option value={p.name}>{p.name} ({p.modality})</option>
          {/each}
        </select>
      </label>
      <label>
        <span>New name</span>
        <input bind:value={cloneName} placeholder="coder_cheap" required pattern="[A-Za-z0-9_\-]+" />
      </label>
      <label>
        <span>Purpose</span>
        <input bind:value={clonePurpose} placeholder="what this seat is for" />
      </label>
      <label>
        <span>Token</span>
        <input type="password" bind:value={token} placeholder="profiles:write" autocomplete="off" />
      </label>
    </div>
    <button type="submit" class="primary" disabled={busy || !cloneFrom || !cloneName.trim()}>
      {busy ? 'Creating…' : 'Create'}
    </button>
    {#if error}<p class="error">{error.message}</p>{/if}
  </form>
{/if}

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
  .top {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .new {
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel2);
    color: inherit;
    font: inherit;
    font-size: 0.82rem;
    padding: 0.35rem 0.8rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .clone {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.9rem 1rem 1rem;
    margin: 0.5rem 0 1.2rem;
  }
  .clone .hint {
    margin: 0 0 0.8rem;
    color: var(--muted);
    font-size: 0.78rem;
    line-height: 1.5;
    max-width: 60ch;
  }
  .fields {
    display: grid;
    gap: 0.7rem;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  }
  .fields label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.78rem;
    min-width: 0;
  }
  .fields span {
    color: var(--muted);
  }
  .fields input,
  .fields select {
    padding: 0.32rem 0.45rem;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--bg);
    color: inherit;
    font: inherit;
    font-size: 0.82rem;
    min-width: 0;
  }
  .clone button.primary {
    margin-top: 0.9rem;
    padding: 0.35rem 1rem;
    border: 1px solid var(--ink);
    border-radius: 6px;
    background: var(--ink);
    color: var(--bg);
    font: inherit;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .clone button.primary:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .error {
    margin: 0.6rem 0 0;
    color: #c53030;
    font-size: 0.8rem;
  }
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
