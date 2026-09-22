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
  import { explainError } from '$lib/api/client';
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
  {:else if store.failed}
    <!-- A read that failed is said out loud: `bar()` draws a reading, and a
         failed read has no reading to draw. The last good one stays beside it,
         because the server did answer. -->
    <span class="item bad">Cannot read the status: {explainError(store.failed)}</span>
  {/if}
  {#if view}
    {#each view.items as item (item.key)}
      {#if item.key === 'summary'}
        <a
          class="item link summary"
          data-key={item.key}
          href="/runs"
          title={item.title ?? item.text}
          aria-label={item.text}
        >{item.text}</a>
      {:else}
        <span class="item {item.tone}" data-key={item.key}>
          {#if item.key === 'run'}<span class="dot" aria-hidden="true"></span>{/if}
          {item.text}
        </span>
      {/if}
    {/each}
    {#if view.unscored !== null}
      <a class="item link" data-key="unscored" href="/unscored"
        >{view.unscored.toLocaleString('en-US')} unscored</a
      >
    {/if}
    {#if view.next}<span class="item right" data-key="next">{view.next}</span>{/if}
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
    /* The summary yields the remaining width; every operational item keeps its
       full width, so the bar itself never makes the page scroll sideways. */
    overflow: hidden;
  }
  .item {
    display: flex;
    align-items: center;
    flex: 0 0 auto;
    gap: 6px;
    white-space: nowrap;
  }
  .item.summary {
    display: block;
    min-width: 0;
    overflow: hidden;
    flex: 1 1 auto;
    line-height: var(--h-status);
    text-overflow: ellipsis;
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

  /* §6.8: at 375 the bar is wider than the screen, so it scrolled and the last
     item was cut mid-word at the edge. The phone keeps what is worth a phone's
     width -- the run state and the way to the unscored models -- and the rest
     is on the desktop. A failure is never dropped: it carries no `data-key`. */
  @media (max-width: 899px) {
    .item[data-key]:not([data-key='run']):not([data-key='unscored']) {
      display: none;
    }

    .status {
      gap: 12px;
    }
  }
</style>
