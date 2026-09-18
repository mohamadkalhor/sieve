<script lang="ts">
  import { api, type ApiError } from '$lib/api/client';
  import { blocks, inline } from '$lib/markdown';

  let markdown = $state('');
  let error = $state<ApiError | null>(null);
  let loading = $state(true);
  $effect(() => {
    void api.guide().then((result) => {
      if (result.ok) markdown = result.value;
      else error = result.error;
      loading = false;
    });
  });
</script>

{#snippet text(value: string)}
  {#each inline(value) as part}
    {#if part.kind === 'code'}<code>{part.text}</code>
    {:else if part.kind === 'strong'}<strong>{part.text}</strong>
    {:else}{part.text}{/if}
  {/each}
{/snippet}

<svelte:head><title>Guide · Sieve</title></svelte:head>

<h1>Guide</h1>
<p class="lede">
  Sieve ranks every model it can reach against what each seat cares about, and ships the winning
  list to your gateways. It does that in three steps, and you can run any of them by hand.
</p>

<section class="operating">
  {#if loading}<p>Loading operating guide…</p>
  {:else if error}<p class="error">{error.message}</p>
  {:else}
    {#each blocks(markdown) as block}
      {#if block.kind === 'heading'}
        <svelte:element this={`h${Math.min(block.level + 1, 6)}`}>{@render text(block.text)}</svelte:element>
      {:else if block.kind === 'code'}<pre><code>{block.text}</code></pre>
      {:else if block.kind === 'paragraph'}<p>{@render text(block.text)}</p>
      {:else if block.kind === 'list'}
        {#if block.ordered}<ol>{#each block.items as item}<li>{@render text(item)}</li>{/each}</ol>
        {:else}<ul>{#each block.items as item}<li>{@render text(item)}</li>{/each}</ul>{/if}
      {/if}
    {/each}
  {/if}
</section>

<section>
  <h2>The screens</h2>
  <ul class="screens">
    <li><a href="/field">Overview</a> — every model in one modality, quality against cost.</li>
    <li><a href="/profiles">Profiles</a> — one row per seat: its controls and the list they produce.</li>
    <li><a href="/axes">Axes</a> — what a score is made of, and which fields carry it.</li>
    <li><a href="/connectors">Connectors</a> — the gateways Sieve reads and writes, and the cost multipliers per prefix.</li>
    <li><a href="/runs">Runs</a> — what the loop has done.</li>
    <li><a href="/sources">Sources</a> — what each benchmark last published, and whether its key is set.</li>
    <li><a href="/pulse">Pulse</a> — what your own traffic says about the models you are shipping.</li>
  </ul>
</section>

<section>
  <h2>Signing in</h2>
  <p>
    Sieve asks <a href="/auth/login" rel="external">gate</a> who you are — sieve's own instance of it, on this
    same hostname, not a shared login somewhere else. When you are signed in, every screen writes
    as you and no token is asked for: an owner or a member can change any seat and ship it, a
    viewer can change her own private copies but never ship one. A script uses a bearer token
    instead, with the scope the call needs — reads are open over the API's own connection, changing
    a seat needs <code>profiles:write</code>, shipping needs <code>apply</code>.
  </p>
</section>

<style>
  h1 {
    margin: 0 0 0.2rem;
    font-size: 1.6rem;
  }
  h2 {
    font-family: var(--ui);
    font-size: 0.95rem;
    margin: 0 0 0.4rem;
  }
  .lede {
    margin: 0 0 1.4rem;
    color: var(--muted);
    font-size: 0.85rem;
    max-width: 46rem;
  }
  section {
    margin-bottom: 1.6rem;
    max-width: 46rem;
  }
  .operating { font-size: 0.85rem; line-height: 1.65; overflow-wrap: anywhere; }
  .operating :global(h2), .operating :global(h3) { margin-top: 1.4rem; font-family: var(--ui); }
  pre { overflow-x: auto; padding: 0.8rem; background: var(--panel2); border: 1px solid var(--rule); border-radius: var(--radius); }
  .error { color: var(--bad); }
  p {
    color: var(--muted);
    font-size: 0.8rem;
    line-height: 1.55;
  }
  .screens {
    list-style: none;
    margin: 0;
    padding: 0;
    color: var(--muted);
    font-size: 0.8rem;
    line-height: 1.7;
  }
  a {
    color: var(--accent);
  }
  code {
    font-family: var(--mono);
    font-size: 0.76rem;
  }
</style>
