<script lang="ts">
  /**
   * Adding an axis the profile does not score by yet (CONSOLE.md section 6.4).
   *
   * Only the axes the server says are spare for this modality: an axis the
   * profile already carries is not an offer, and one the server does not know
   * would not score anything.
   */
  import type { AxisRow } from '$lib/api/client';
  import Popover from '$lib/console/ui/Popover.svelte';

  interface Props {
    open: boolean;
    anchor: HTMLElement | null;
    spare: AxisRow[];
    onpick: (axis: string) => void;
    onclose: () => void;
  }

  let { open, anchor, spare, onpick, onclose }: Props = $props();

  function pick(axis: string): void {
    onpick(axis);
    onclose();
  }
</script>

<Popover {open} {anchor} {onclose} label="Add an axis" width={280}>
  <ul class="list">
    {#each spare as row (row.name)}
      <li>
        <button type="button" class="row" onclick={() => pick(row.name)}>
          <span class="name">{row.label}</span>
          {#if row.meaning}<span class="meaning">{row.meaning}</span>{/if}
        </button>
      </li>
    {/each}
  </ul>
</Popover>

<style>
  .list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 300px;
    overflow: auto;
  }

  .row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
    padding: 6px 8px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    text-align: left;
    cursor: pointer;
  }

  .row:hover {
    background: var(--c-raised);
  }

  .name {
    font-size: 12px;
    font-weight: 600;
    color: var(--c-ink);
  }

  .meaning {
    font-size: 11px;
    line-height: 1.4;
    color: var(--c-muted);
  }
</style>
