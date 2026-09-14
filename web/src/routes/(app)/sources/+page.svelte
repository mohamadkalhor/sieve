<script lang="ts">
  /** Sources: what has been pulled, and the ids nothing could be matched to. */
  import { api, type AliasRow, type ApiError, type ModelRow, type SourceRow } from '$lib/api/client';
  import type { Reachable } from '$lib/types';
  import Chip from '$lib/components/Chip.svelte';
  import Empty from '$lib/components/Empty.svelte';

  let sources = $state<SourceRow[]>([]);
  let unmatched = $state<Reachable[]>([]);
  let aliases = $state<AliasRow[]>([]);
  let models = $state<ModelRow[]>([]);
  let choices = $state<Record<string, string>>({});
  let error = $state<ApiError | null>(null);
  let token = $state('');
  let notice = $state('');
  let busy = $state('');
  let loading = $state(true);

  async function load() {
    error = null;
    const [s, i, a] = await Promise.all([api.sources(), api.inventory(true), api.aliases()]);
    if (s.ok) sources = s.value;
    else error = s.error;
    if (i.ok) unmatched = i.value;
    else error = i.error;
    if (a.ok) aliases = a.value.filter((row) => row.origin === 'user');
    else error = a.error;
    const catalogue: ModelRow[] = [];
    let cursor: string | undefined;
    do {
      const page = await api.models({ limit: 500, cursor });
      if (!page.ok) {
        error = page.error;
        break;
      }
      catalogue.push(...page.value.items);
      cursor = page.value.next_cursor ?? undefined;
    } while (cursor);
    models = catalogue;
    loading = false;
  }

  $effect(() => {
    void load();
  });

  async function saveAlias(item: Reachable) {
    const model = models.find((m) => `${m.modality}:${m.id}` === choices[item.local_id]);
    if (!model) return;
    busy = `alias:${item.local_id}`;
    const result = await api.alias(item.local_id, model.id, model.modality, { token: token || undefined });
    busy = '';
    if (!result.ok) { error = result.error; return; }
    notice = `${item.local_id} aliased as ${model.id}.`;
    await load();
  }

  async function removeAlias(row: AliasRow) {
    busy = `alias:${row.alias}`;
    const result = await api.removeAlias(row.alias, row.modality, { token: token || undefined });
    busy = '';
    if (!result.ok) { error = result.error; return; }
    notice = `${row.alias} removed.`;
    await load();
  }

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
  <span>Token (apply to pull; profiles:write for aliases)</span>
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
          <button type="button" onclick={() => pull(source.name)}
            disabled={!source.enabled || busy === source.name}
            title={!source.enabled ? 'This source is disabled; enable it before pulling.' : 'Pull this source'}>
            {busy === source.name ? 'pulling…' : 'Pull now'}
          </button>
        </header>
        <!--
          Prices count as a pull. A source that supplies prices and no
          observations used to read "0 observations, last pull never" on the
          morning it had pulled forty-eight thousand prices, so "never" now
          means it wrote to neither.
        -->
        <div class="rows num">
          {source.rows.toLocaleString()} observations{#if source.prices}{` · ${source.prices.toLocaleString()} prices`}{/if}
        </div>
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
        <form onsubmit={(event) => { event.preventDefault(); void saveAlias(item); }}>
          <label>Alias as
            <select bind:value={choices[item.local_id]} required>
              <option value="">Choose a model</option>
              {#each models as model (`${model.modality}:${model.id}`)}
                <option value={`${model.modality}:${model.id}`}>{model.name} · {model.modality} · {model.id}</option>
              {/each}
            </select>
          </label>
          <button type="submit" disabled={!choices[item.local_id] || busy === `alias:${item.local_id}`}>Save</button>
        </form>
      </li>
    {/each}
  </ul>
{/if}

<h2>User aliases</h2>
<p class="muted small">Removing an alias undoes its inventory match. Source aliases may also be deleted through the API, but the next source pull may recreate them.</p>
<ul class="unmatched">
  {#each aliases as row (`${row.modality}:${row.alias}`)}
    <li>
      <span class="mono">{row.alias} → {row.model_id}</span>
      <span class="from">{row.modality}</span>
      <button type="button" disabled={busy === `alias:${row.alias}`} onclick={() => void removeAlias(row)}>Remove</button>
    </li>
  {:else}
    <li class="muted">No user aliases.</li>
  {/each}
</ul>

<style>
  form { display: flex; gap: 0.4rem; align-items: center; }
  select { background: var(--panel2); color: var(--ink); border: 1px solid var(--rule); border-radius: 7px; font: inherit; padding: 0.3rem; max-width: 26rem; }
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
