<script lang="ts">
  import type { Chain } from '$lib/types';

  interface Props {
    chain: Chain;
    reachable?: ReadonlySet<string>;
  }
  let { chain, reachable = new Set<string>() }: Props = $props();

  const models = $derived([chain.primary, ...(chain.fallbacks ?? [])]);
  const since = $derived(
    chain.incumbent_since
      ? Math.max(0, Math.round((Date.now() - Date.parse(chain.incumbent_since)) / 86_400_000))
      : null
  );
</script>

<article class="card">
  <header>
    <a class="name" href={`/chains/${encodeURIComponent(chain.profile)}`}>{chain.profile}</a>
    {#if since !== null}
      <span class="tenure mono">{since} d in the seat</span>
    {/if}
  </header>

  <ol>
    {#each models as model, index (model)}
      <li>
        <span class="pos num">{index}</span>
        <span class="id mono">{model}</span>
        <span
          class="dot"
          class:on={reachable.size === 0 || reachable.has(model)}
          title={reachable.size === 0 || reachable.has(model) ? 'reachable' : 'not reachable'}
        ></span>
      </li>
    {/each}
  </ol>
</article>

<style>
  .card {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.85rem;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }
  .name {
    font-family: var(--display);
    font-size: 1rem;
  }
  .name:hover {
    color: var(--accent);
  }
  .tenure {
    color: var(--muted);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  li {
    display: grid;
    grid-template-columns: 1.3rem 1fr auto;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.82rem;
  }
  li:first-child .id {
    color: var(--accent);
  }
  .pos {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .id {
    overflow-wrap: anywhere;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--rule);
  }
  .dot.on {
    background: var(--reach);
  }
</style>
