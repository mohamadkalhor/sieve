<script lang="ts">
  /**
   * Profiles: every seat your agents play, managed from one list.
   *
   * This screen replaces three. Profiles was a grid of cards you clicked into;
   * Rankings was the list one of those cards produced; Chains was what that
   * list had last shipped. They were the same object seen three times, and
   * answering "is this seat right?" meant holding all three in your head.
   *
   * Here a profile is one row: the card, its controls, and the list those
   * controls produce, side by side. Moving a weight re-ranks the list beside
   * it; the card says whether what you are looking at is what the gateway is
   * actually serving. The old URLs redirect here with the profile opened.
   */
  import { page } from '$app/stores';
  import { api, explainError, type ApiError } from '$lib/api/client';
  import type { Modality, Profile } from '$lib/types';
  import Empty from '$lib/components/Empty.svelte';
  import ProfileRow from '$lib/components/ProfileRow.svelte';

  let profiles = $state<Profile[]>([]);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);
  let notice = $state('');
  let token = $state('');

  /** the defaults from `/v1/cost-multipliers`; null where the route is absent */
  let defaults = $state<Record<string, number> | null>(null);
  let modalities = $state<Modality[]>([]);

  /** which row is opened to its deeper settings; only ever one */
  let opened = $state<string | null>(null);
  let collapsed = $state<Set<string>>(new Set());

  /** one ticker for the whole screen, rather than one per row */
  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  /*
    `/rankings/coder` and `/chains/coder` redirect to `/profiles?open=coder`,
    so a bookmark from either of the screens this one replaced still lands on
    the thing it was pointing at.
  */
  $effect(() => {
    const wanted = $page.url.searchParams.get('open');
    if (wanted) opened = wanted;
  });

  async function load() {
    loading = true;
    const [found, defaulted, counted] = await Promise.all([
      api.profiles(),
      api.costMultipliers(),
      api.modalities()
    ]);
    loading = false;

    if (!found.ok) {
      error = found.error;
      return;
    }
    error = null;
    profiles = Array.isArray(found.value) ? found.value : [];

    // absent, rather than broken: the route lands with the settings module
    defaults =
      defaulted.ok && defaulted.value && typeof defaulted.value === 'object'
        ? defaulted.value
        : null;

    modalities =
      counted.ok && Array.isArray(counted.value)
        ? counted.value.map((row) => row.modality)
        : [...new Set(profiles.map((p) => p.modality))];
  }

  $effect(() => {
    void load();
  });

  const byModality = $derived.by(() => {
    const grouped = new Map<string, Profile[]>();
    for (const profile of profiles) {
      grouped.set(profile.modality, [...(grouped.get(profile.modality) ?? []), profile]);
    }
    for (const [, group] of grouped) group.sort((a, b) => a.name.localeCompare(b.name));
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  });

  function toggleGroup(modality: string) {
    const next = new Set(collapsed);
    if (next.has(modality)) next.delete(modality);
    else next.add(modality);
    collapsed = next;
  }

  /* ---------------------------------------------------------------------- */
  /* a new profile                                                           */
  /* ---------------------------------------------------------------------- */

  let adding = $state(false);
  let newName = $state('');
  let newModality = $state<Modality>('llm');
  let copyFrom = $state('');
  let creating = $state(false);
  let formError = $state('');

  const choices = $derived(
    modalities.length ? modalities : ([...new Set(profiles.map((p) => p.modality))] as Modality[])
  );

  /**
   * Create.
   *
   * Cloning rather than starting empty is what anybody actually does: a
   * profile is weights that sum to 1 over axes that exist for its modality,
   * plus constraints, a shape and a policy, and assembling that from nothing
   * is an exercise in reading error messages.
   */
  async function create(event: SubmitEvent) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) {
      formError = 'Give it a name.';
      return;
    }
    creating = true;
    formError = '';
    const options = { token: token || undefined };

    const result = await api.newProfile(
      {
        name,
        modality: newModality,
        from: copyFrom || undefined,
        copy_from: copyFrom || undefined
      },
      options
    );
    creating = false;

    if (!result.ok) {
      formError = explainError(result.error);
      return;
    }
    adding = false;
    newName = '';
    copyFrom = '';
    notice = `${name} created.`;
    opened = name;
    await load();
  }
</script>

<svelte:head><title>Profiles · Sieve</title></svelte:head>

<header class="top">
  <div>
    <h1>Profiles</h1>
    <p class="lede">
      One row per seat your agents play: what it is, the controls that shape it, and the list those
      controls would ship. Move a weight and the list beside it moves; click a card for everything
      else.
    </p>
  </div>
  <button type="button" class="new" onclick={() => (adding = !adding)} aria-expanded={adding}>
    {adding ? 'Cancel' : 'New profile'}
  </button>
</header>

<label class="token">
  <span>Token (needed to change anything)</span>
  <input
    type="password"
    bind:value={token}
    placeholder="a token with profiles:write and apply"
    autocomplete="off"
  />
</label>

{#if adding}
  <form class="add" onsubmit={create}>
    <div class="fields">
      <label>
        <span>Name</span>
        <input bind:value={newName} placeholder="coder_cheap" pattern="[A-Za-z0-9_\-]+" required />
      </label>
      <label>
        <span>Modality</span>
        <select bind:value={newModality}>
          {#each choices as modality (modality)}
            <option value={modality}>{modality}</option>
          {/each}
        </select>
      </label>
      <label>
        <span>Copy from <small>optional</small></span>
        <select bind:value={copyFrom}>
          <option value="">nothing — start empty</option>
          {#each profiles as profile (profile.name)}
            <option value={profile.name}>{profile.name} ({profile.modality})</option>
          {/each}
        </select>
      </label>
    </div>
    {#if formError}<p class="error">{formError}</p>{/if}
    <button type="submit" class="primary" disabled={creating || !newName.trim()}>
      {creating ? 'Creating…' : 'Create'}
    </button>
  </form>
{/if}

{#if notice}<p class="notice">{notice}</p>{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if profiles.length === 0}
  <Empty {error} title="No profiles" hint="Add a YAML file under profiles/&lt;modality&gt;/." />
{/if}

{#each byModality as [modality, group] (modality)}
  <section>
    <h2>
      <button
        type="button"
        class="group"
        aria-expanded={!collapsed.has(modality)}
        onclick={() => toggleGroup(modality)}
      >
        <span class="caret" aria-hidden="true">{collapsed.has(modality) ? '▸' : '▾'}</span>
        {modality}
        <span class="count">{group.length}</span>
      </button>
    </h2>
    {#if !collapsed.has(modality)}
      <ul class="rows">
        {#each group as profile (profile.name)}
          <ProfileRow
            {profile}
            {token}
            {defaults}
            {now}
            open={opened === profile.name}
            ontoggle={() => (opened = opened === profile.name ? null : profile.name)}
            onchanged={(message) => {
              notice = message;
              opened = null;
              void load();
            }}
          />
        {/each}
      </ul>
    {/if}
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
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 1rem;
    max-width: 64ch;
  }
  .new {
    border: 1px solid var(--accent);
    border-radius: 6px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 0.8rem;
    padding: 0.3rem 0.8rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.76rem;
    color: var(--muted);
    max-width: 22rem;
    margin-bottom: 0.8rem;
  }
  .token input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
  }
  .add {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.8rem 0.9rem 0.9rem;
    margin-bottom: 1rem;
  }
  .fields {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: 0.6rem 0.8rem;
  }
  .fields label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.76rem;
    color: var(--muted);
    min-width: 0;
  }
  .fields input,
  .fields select {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
    font-size: 0.8rem;
    min-width: 0;
  }
  .primary {
    margin-top: 0.7rem;
    background: var(--panel2);
    border: 1px solid var(--accent);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.8rem;
    padding: 0.25rem 0.9rem;
    cursor: pointer;
  }
  .primary:disabled {
    opacity: 0.5;
    cursor: default;
  }
  h2 {
    margin: 1.2rem 0 0.5rem;
  }
  .group {
    display: inline-flex;
    align-items: baseline;
    gap: 0.4rem;
    background: none;
    border: none;
    padding: 0.1rem 0.2rem;
    color: var(--muted);
    font-family: var(--ui);
    font-size: 0.76rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .group:hover {
    color: var(--ink);
  }
  .caret {
    font-size: 0.7rem;
  }
  .count {
    color: var(--muted);
    opacity: 0.7;
    letter-spacing: 0;
  }
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .muted {
    color: var(--muted);
  }
  .notice {
    color: var(--good);
    font-size: 0.8rem;
  }
  .error {
    color: var(--bad);
    font-size: 0.8rem;
    margin: 0.5rem 0 0;
    overflow-wrap: anywhere;
  }
</style>
