<script lang="ts">
  /**
   * The status bar (CONSOLE.md section 6.1).
   *
   * One line about the server, and the whole of it comes from one place: what
   * the last run did, what the gateway can reach, what is waiting to be scored,
   * when the next run is due. Every item is a field the server actually sent --
   * `bar()` decides that, and this file only draws it.
   *
   * The clock is the bar's own: `ago()` is asked again every half minute, so
   * "14 min ago" keeps meaning 14 min ago rather than freezing at mount.
   */
  import { status as statusStore } from '../context';
  import { bar } from './statusbar';

  const store = statusStore();

  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  const view = $derived(store.row && !store.unreachable ? bar(store.row, now) : null);
</script>

<footer class="status" aria-label="Status">
  {#if store.unreachable}
    <span class="item bad">Cannot reach the API</span>
  {:else if view}
    {#each view.items as item (item.key)}
      <span class="item {item.tone}">
        {#if item.key === 'run'}<span class="dot" aria-hidden="true"></span>{/if}
        {item.text}
      </span>
    {/each}
    {#if view.unscored !== null}
      <a class="item link" href="/unscored">{view.unscored.toLocaleString('en-US')} unscored</a>
    {/if}
    {#if view.next}<span class="item right">{view.next}</span>{/if}
  {/if}
</footer>

<style>
  .status {
    display: flex;
    align-items: center;
    gap: 16px;
    height: var(--h-status);
    padding: 0 12px;
    border-top: 1px solid var(--c-rule);
    background: var(--c-bg);
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
    /* The row is one line of a fixed height, so on a narrow screen it scrolls
       inside itself rather than widening the page. Clipping would be quieter
       and would hide a field the server did send. */
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
  }
  .status::-webkit-scrollbar {
    display: none;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .item.right {
    margin-left: auto;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--c-accent);
  }
  .ok .dot {
    background: var(--c-accent);
  }
  .bad {
    color: var(--c-bad);
  }
  .bad .dot {
    background: var(--c-bad);
  }
  .warn {
    color: var(--c-warn);
  }
  .warn .dot {
    background: var(--c-warn);
  }
  .link {
    color: var(--c-muted);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .link:hover {
    color: var(--c-ink);
  }
  .link:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: 1px;
  }
</style>
