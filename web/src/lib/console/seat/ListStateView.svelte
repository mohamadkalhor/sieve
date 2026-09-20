<script lang="ts">
  /**
   * The five ways the table's body can have nothing to show (CONSOLE.md section
   * 6.6, and `lib/profile/tune.ts` for the state itself).
   *
   * A list that is still arriving is not an empty list, and a request that
   * failed did not empty the list either: the rows of the last answer stay on
   * screen, dimmed, behind the message. Only `empty` -- an answer that came
   * back with nothing in it -- is allowed to draw a sentence instead of rows,
   * and the sentence is handed in already worded, so the judgement about what
   * may be claimed lives with the data rather than here.
   */
  import type { Snippet } from 'svelte';
  import type { ListState } from '$lib/profile/tune';
  import { retryable } from '$lib/profile/tune';
  import Button from '$lib/console/ui/Button.svelte';
  import Skeleton from '$lib/console/ui/Skeleton.svelte';

  interface Props {
    state: ListState;
    /** what the last request failed with, when it did */
    failed?: string | null;
    /** how long the request in flight has been in flight */
    waitedMs?: number;
    /** the sentence an empty answer is allowed to be read as */
    empty: string;
    onretry: () => void;
    children?: Snippet;
  }

  let { state, failed = null, waitedMs = 0, empty, onretry, children }: Props = $props();

  const waited = $derived((waitedMs / 1000).toFixed(1));
</script>

{#if state === 'ranking'}
  <div class="note" role="status">
    <span class="caps">Ranking…</span>
  </div>
  <Skeleton rows={4} height={44} />
{:else if state === 'busy'}
  <div class="note" role="status">
    <span>The server is busy; still ranking…</span>
    <span class="waited num">{waited}s</span>
    {#if retryable(state)}
      <Button variant="ghost" size="sm" onclick={onretry}>Retry</Button>
    {/if}
  </div>
{:else if state === 'error'}
  <div class="note" role="alert">
    <span>{failed ?? 'The list could not be read.'}</span>
    {#if retryable(state)}
      <Button variant="ghost" size="sm" onclick={onretry}>Retry</Button>
    {/if}
  </div>
  <div class="dim">{@render children?.()}</div>
{:else if state === 'empty'}
  <p class="empty">{empty}</p>
  <!--
    An empty lineup is not an empty table: the models a need kept out, the ones
    removed by hand and the ones nothing reachable serves are still worth
    reading, and they are what explains the sentence above.
  -->
  {@render children?.()}
{:else}
  {@render children?.()}
{/if}

<style>
  .note {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 40px;
    padding: 0 20px;
    font-size: 12px;
    color: var(--c-muted);
  }

  .waited {
    font-size: 11px;
  }

  .dim {
    opacity: 0.4;
  }

  .empty {
    margin: 0;
    padding: 14px 20px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--c-muted);
    max-width: 62ch;
  }
</style>
