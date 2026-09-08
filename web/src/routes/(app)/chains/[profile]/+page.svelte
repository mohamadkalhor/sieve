<script lang="ts">
  import { page } from '$app/stores';
  import { api, type ApiError } from '$lib/api/client';
  import type { Chain, Decision, TargetDiff } from '$lib/types';
  import ChainCard from '$lib/components/ChainCard.svelte';
  import Diff from '$lib/components/Diff.svelte';
  import Empty from '$lib/components/Empty.svelte';
  import Timeline from '$lib/components/Timeline.svelte';

  let chain = $state<Chain | null>(null);
  let decisions = $state<Decision[]>([]);
  let diffs = $state<TargetDiff[]>([]);
  let error = $state<ApiError | null>(null);
  let token = $state('');
  let notice = $state('');
  let busy = $state(false);
  let loading = $state(true);

  const name = $derived($page.params.profile ?? '');

  $effect(() => {
    const wanted = name;
    loading = true;
    Promise.all([api.chain(wanted), api.decisions(wanted), api.diff()]).then(([c, d, t]) => {
      if (wanted !== name) return;
      chain = c.ok ? c.value : null;
      if (!c.ok) error = c.error;
      decisions = d.ok ? d.value : [];
      diffs = t.ok ? t.value : [];
      loading = false;
    });
  });

  /**
   * What each target holds *now*, read from the target itself.
   *
   * This used to read the last `apply` decision, which only says what Sieve
   * believes it wrote. A target edited by hand, rolled back, or written by
   * something else showed no difference at all against that belief — which is
   * exactly the moment a diff has to be right.
   */
  const targets = $derived(
    diffs.map((d) => ({
      ...d,
      before: d.current?.[name] ?? []
    }))
  );

  const computed = $derived(chain ? [chain.primary, ...(chain.fallbacks ?? [])] : []);

  async function apply() {
    busy = true;
    notice = '';
    const result = await api.apply([name], { token: token || undefined });
    busy = false;
    if (!result.ok) {
      error = result.error;
      return;
    }
    error = null;
    notice = `Applied to ${result.value.map((t) => t.target).join(', ') || 'no target'}.`;
    const again = await api.decisions(name);
    if (again.ok) decisions = again.value;
  }
</script>

<svelte:head><title>{name} · Chains · Sieve</title></svelte:head>

<h1>{name}</h1>

{#if loading}
  <p class="muted">Loading…</p>
{:else if !chain}
  <Empty {error} title="No chain yet" hint="Run `sieve plan --store` for this profile." />
{:else}
  <div class="cols">
    <div>
      <ChainCard {chain} />

      <h2>Against what each target holds now</h2>
      {#if targets.length === 0}
        <p class="muted small">No targets configured, so there is nothing to compare against.</p>
      {/if}
      {#each targets as t (t.target)}
        {#if t.supported === false}
          <p class="muted small">
            <strong>{t.target}</strong> ({t.kind}) cannot be read back{t.error ? `: ${t.error}` : ''}.
            Not the same as holding nothing.
          </p>
        {:else}
          <Diff before={t.before} after={computed} target={`${t.target} (${t.kind})`} />
        {/if}
      {/each}

      <h2>Apply</h2>
      <p class="muted small">
        Writes {computed.length} model{computed.length === 1 ? '' : 's'} to
        {#if targets.length === 0}
          no target — nothing is configured.
        {:else}
          {#each targets as t, i (t.target)}<strong>{t.target}</strong> ({t.kind}){i <
            targets.length - 1
              ? ', '
              : ''}{/each}.
        {/if}
      </p>
      {#if targets.some((t) => t.supported !== false && (t.current?.[name]?.length ?? 0) === 0)}
        <p class="muted small">
          At least one target holds nothing for this profile yet, so this is the first write.
        </p>
      {/if}
      <label class="token">
        <span>Token</span>
        <input
          type="password"
          bind:value={token}
          placeholder="a token with apply"
          autocomplete="off"
        />
      </label>
      <button type="button" onclick={apply} disabled={busy}>Apply</button>
      {#if notice}<p class="notice">{notice}</p>{/if}
      {#if error}<p class="error">{error.message}</p>{/if}
    </div>

    <div>
      <h2>Decisions</h2>
      {#if decisions.length}
        <Timeline {decisions} />
      {:else}
        <p class="muted small">Nothing decided yet.</p>
      {/if}
    </div>
  </div>
{/if}

<style>
  h1 {
    font-size: 1.6rem;
    margin: 0 0 1rem;
  }
  h2 {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-family: var(--ui);
    margin: 1.5rem 0 0.5rem;
  }
  .cols {
    display: grid;
    grid-template-columns: minmax(0, 22rem) minmax(0, 1fr);
    gap: 1.75rem;
    align-items: start;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 0.8rem;
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.78rem;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }
  .token input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
  }
  button {
    background: var(--panel2);
    border: 1px solid var(--accent);
    border-radius: 7px;
    color: var(--accent);
    font: inherit;
    font-size: 0.82rem;
    padding: 0.3rem 0.9rem;
    cursor: pointer;
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
  @media (max-width: 900px) {
    .cols {
      grid-template-columns: 1fr;
    }
  }
</style>
