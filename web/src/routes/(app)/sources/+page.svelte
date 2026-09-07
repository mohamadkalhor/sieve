<script lang="ts">
  /** Sources: what has been pulled, and the ids nothing could be matched to. */
  import { api, type ApiError, type SourceRow } from '$lib/api/client';
  import type { Reachable } from '$lib/types';
  import Chip from '$lib/components/Chip.svelte';
  import Empty from '$lib/components/Empty.svelte';

  let sources = $state<SourceRow[]>([]);
  let unmatched = $state<Reachable[]>([]);
  let error = $state<ApiError | null>(null);
  let token = $state('');
  let notice = $state('');
  let busy = $state('');
  let loading = $state(true);

  async function load() {
    const [s, i] = await Promise.all([api.sources(), api.inventory(true)]);
    if (s.ok) sources = s.value;
    else error = s.error;
    unmatched = i.ok ? i.value : [];
    loading = false;
  }

  $effect(() => {
    void load();
  });

  async function pull(name: string) {
    busy = name;
    notice = '';
    const result = await api.pull(name, { token: token || undefined });
    busy = '';
    if (!result.ok) {
      error = result.error;
      return;
    }
    error = null;
    notice = `${name}: ${result.value.added} new observation${result.value.added === 1 ? '' : 's'}.`;
    await load();
  }

  const when = (value: string | null) =>
    value ? new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : 'never';
</script>

<svelte:head><title>Sources · Sieve</title></svelte:head>

<h1>Sources</h1>
<p class="lede">Where the numbers come from, and what could not be matched to a model.</p>

<label class="token">
  <span>Token (needed to pull)</span>
  <input type="password" bind:value={token} placeholder="a token with apply" autocomplete="off" />
</label>
{#if notice}<p class="notice">{notice}</p>{/if}
{#if error}<p class="error">{error.message}</p>{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if sources.length === 0}
  <Empty {error} title="No sources configured" hint="Add a [sources.*] block to sieve.toml." />
{:else}
  <div class="grid">
    {#each sources as source (source.name)}
      <article class="card" class:off={!source.enabled}>
        <header>
          <span class="name mono">{source.name}</span>
          <button type="button" onclick={() => pull(source.name)} disabled={busy === source.name}>
            {busy === source.name ? 'pulling…' : 'Pull now'}
          </button>
        </header>
        <div class="rows num">{source.rows.toLocaleString()} observations</div>
        <div class="when">last pull {when(source.last_pull)}</div>
        <div class="chips">
          <Chip label="enabled" value={source.enabled} tone={source.enabled ? 'reach' : 'warn'} />
          <Chip
            label="installed"
            value={source.registered}
            tone={source.registered ? 'muted' : 'warn'}
          />
          {#if source.needs_key}
            <Chip
              label="key"
              value={source.key_present ? 'present' : 'missing'}
              tone={source.key_present ? 'muted' : 'warn'}
            />
          {:else}
            <Chip label="key" value="not needed" />
          {/if}
        </div>
        <div class="modalities">{source.modalities.join(' · ') || 'any modality'}</div>
      </article>
    {/each}
  </div>
{/if}

<h2>Unmatched inventory ids</h2>
{#if unmatched.length === 0}
  <p class="muted small">
    Every model your gateways serve is matched to a catalogue entry.
  </p>
{:else}
  <p class="muted small">
    These are served by a gateway but Sieve would not guess which model they are. Add them to
    <code>data/aliases.yaml</code>, or alias one here.
  </p>
  <ul class="unmatched">
    {#each unmatched as item (item.inventory + item.local_id)}
      <li>
        <span class="mono">{item.local_id}</span>
        <span class="from">{item.inventory}</span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 1rem;
    max-width: 62ch;
  }
  h2 {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-family: var(--ui);
    margin: 2rem 0 0.5rem;
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.78rem;
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
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
    gap: 0.6rem;
  }
  .card {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.8rem 0.9rem;
  }
  .card.off {
    opacity: 0.55;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
  }
  .name {
    font-size: 0.95rem;
  }
  button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--muted);
    font: inherit;
    font-size: 0.72rem;
    padding: 0.15rem 0.55rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    color: var(--ink);
    border-color: var(--accent);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .rows {
    font-size: 1.1rem;
  }
  .when,
  .modalities {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin: 0.5rem 0 0.3rem;
  }
  .unmatched {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .unmatched li {
    display: flex;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.3rem 0;
    border-bottom: 1px solid var(--rule);
    font-size: 0.82rem;
    flex-wrap: wrap;
  }
  .from {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 0.82rem;
    max-width: 62ch;
  }
  .notice {
    color: var(--good);
    font-size: 0.82rem;
  }
  .error {
    color: var(--bad);
    font-size: 0.82rem;
    overflow-wrap: anywhere;
  }
  code {
    background: var(--panel2);
    padding: 0.05rem 0.3rem;
    border-radius: 4px;
    color: var(--ink);
  }
</style>
