<script lang="ts">
  /**
   * Which router ids serve this model (CONSOLE.md section 6.7).
   *
   * An id whose prefix the router has stopped serving is marked and stays on
   * the list: a name that has gone quiet is exactly what somebody is looking
   * for when they open this panel. The trim factor is shown beside the prefix
   * it belongs to, and only when it is not 1 -- a column of `× 1.00` teaches
   * nothing.
   */
  import type { ModelCard } from '$lib/api/client';
  import { twoDecimals } from './view';

  interface Props {
    entries: ModelCard['served_by'];
    /** the seat's trim factor per prefix */
    trim: Record<string, number>;
    /**
     * Whether this list is an answer at all. A card read from the route or
     * built from a preview row carries the ids it serves; an empty list is
     * then the server saying "nothing", which is sayable. Without either, an
     * empty list is only our own ignorance, and it says that instead.
     */
    known: boolean;
  }

  let { entries, trim, known }: Props = $props();
</script>

<section class="block" data-block="served-by">
  <h3>Served by</h3>

  {#if entries.length > 0}
    <ul class="ids">
      {#each entries as entry (entry.local_id)}
        <li class="id" data-stale={entry.stale ? 'true' : 'false'}>
          <span class="local">{entry.local_id}</span>
          {#if entry.prefix in trim && trim[entry.prefix] !== 1}
            <span class="trim" title="this prefix's trim factor">× {twoDecimals(trim[entry.prefix])}</span>
          {/if}
          {#if entry.stale}
            <span class="gone">gone from the router</span>
          {/if}
        </li>
      {/each}
    </ul>
  {:else if known}
    <p class="quiet">Nothing reachable reports serving it.</p>
  {:else}
    <p class="quiet">No list of router ids to show.</p>
  {/if}
</section>

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  h3 {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--c-ink);
  }
  .ids {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .id {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
  }
  .local {
    font-family: var(--f-mono);
    color: var(--c-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .id[data-stale='true'] .local {
    text-decoration: line-through;
  }
  .trim {
    font-family: var(--f-mono);
    color: var(--c-muted);
  }
  .gone {
    font-size: 11px;
    color: var(--c-muted);
    white-space: nowrap;
  }
  .quiet {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--c-muted);
  }
</style>
