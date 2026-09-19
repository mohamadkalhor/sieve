<script lang="ts">
  /**
   * The keyboard hint beside the palette trigger. `Ctrl K`, or `⌘ K` where the
   * command key is the one that works -- telling a Mac reader to press Ctrl is
   * how a shortcut goes unnoticed.
   */
  import type { Snippet } from 'svelte';

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();
</script>

<kbd>{@render children?.()}</kbd>

<style>
  kbd {
    font-family: var(--f-mono);
    font-size: 11px;
    line-height: 1;
    color: var(--c-muted);
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    padding: 4px 6px;
    white-space: nowrap;
  }
</style>
