<script lang="ts">
  /**
   * What happened to this seat (CONSOLE.md section 6.7).
   *
   * It takes the inspector's column rather than floating over the page: the
   * history is about the seat in front of you, and a drawer that covers the
   * pane it describes is the wrong way round.
   */
  import { explainError } from '$lib/api/client';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { ago } from '$lib/freshness';
  import Icon from '$lib/console/ui/Icon.svelte';
  import IconButton from '$lib/console/ui/IconButton.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();
</script>

<div class="drawer">
  <header class="head">
    <h2 class="title">History</h2>
    <IconButton label="Close the history" onclick={() => session.closeHistory()}>
      <Icon name="x" size={12} />
    </IconButton>
  </header>

  {#if session.historyError}
    <p class="quiet">{explainError(session.historyError)}</p>
  {:else if session.historyLoading && !session.historyRows}
    <p class="quiet">Reading the history…</p>
  {:else if !session.historyRows?.length}
    <p class="quiet">Nothing has changed this seat yet.</p>
  {:else}
    <ul class="rows">
      {#each session.historyRows as row, index (index)}
        <li>
          <span class="who">{row.who}</span>
          <span class="when">{ago(new Date(row.when), new Date())}</span>
          <span class="what">{row.what}</span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .drawer {
    height: 100%;
    overflow: auto;
    padding: 14px 12px;
  }

  .head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }

  .title {
    margin: 0;
    flex: 1 1 auto;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
  }

  .rows {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .rows li {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--c-rule);
  }

  .who {
    font-size: 12px;
    color: var(--c-ink-2);
  }

  .when {
    font-size: 11px;
    color: var(--c-muted);
  }

  .what {
    font-size: 12px;
    line-height: 1.4;
    color: var(--c-ink);
  }

  .quiet {
    margin: 0;
    font-size: 12px;
    color: var(--c-muted);
  }
</style>
