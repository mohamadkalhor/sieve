<script lang="ts">
  /**
   * What the seat must support (CONSOLE.md section 6.5).
   *
   * The four needs are always the four the console knows, so the row does not
   * change shape when the pool is empty -- only what it can say about them
   * does. The count is how many reachable models answer yes, taken from the
   * same `pool` the table draws from, so a pill and the rows behind it cannot
   * disagree.
   */
  import { NEEDS, type Need } from '$lib/api/client';
  import { describable, supportCount } from '$lib/console/logic/abilities';
  import { NEED_LABEL, NEED_TAG } from '$lib/console/logic/abilities';
  import { toggleNeed } from '$lib/console/logic/settings';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Pill from '$lib/console/ui/Pill.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const pool = $derived(session.preview?.pool ?? null);
  const counts = $derived(supportCount(pool ?? []));
  const known = $derived(pool !== null && describable(pool));
  const needs = $derived(session.settings?.needs ?? []);

  function title(need: Need): string {
    const total = pool?.length ?? 0;
    return `${NEED_LABEL[need]} — ${counts[need]} of ${total} reachable`;
  }
</script>

<div class="needs">
  <span class="cap">Must</span>
  {#if pool === null}
    <span class="note">waiting for the list…</span>
  {:else if !known}
    <span class="note">no source describes what these models can do</span>
  {:else}
    {#each NEEDS as need (need)}
      <Pill
        label={NEED_TAG[need]}
        pressed={needs.includes(need)}
        count={String(counts[need])}
        title={title(need)}
        onclick={() => session.edit(toggleNeed(session.settings!, need))}
      />
    {/each}
  {/if}
</div>

<style>
  .needs {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .cap {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
    margin-right: 2px;
  }

  .note {
    font-size: 12px;
    color: var(--c-muted);
  }
</style>
