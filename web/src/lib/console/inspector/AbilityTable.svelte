<script lang="ts">
  /**
   * "What it can do, and who says so" (CONSOLE.md section 6.7).
   *
   * Three answers, three colours, and the sources named. The difference the
   * table exists to draw is between the two ways of not knowing:
   *
   * - `unknown` with "no source says" -- nothing answered;
   * - `unknown` with the sources that answered "no" -- something did.
   *
   * A required need in the first state is the sentence at the foot of the
   * table, because the seat counts it as a no and the person reading has to
   * know that before they ship.
   */
  import type { Need } from '$lib/api/client';
  import { UNKNOWN_COUNTS_AS_NO, unknownRequired, type AbilityRow } from './view';

  interface Props {
    rows: AbilityRow[];
    /** the needs this seat requires, for the sentence at the foot */
    needs: readonly Need[];
  }

  let { rows, needs }: Props = $props();

  const explain = $derived(unknownRequired(rows, needs));
</script>

<section class="block" data-block="abilities">
  <h3>What it can do, and who says so</h3>

  <dl class="table">
    {#each rows as row (row.need)}
      <div class="ability" data-need={row.need} data-tone={row.tone}>
        <dt class="label">{row.label}</dt>
        <dd class="answer">{row.tone}</dd>
        <dd class="from">{row.who}</dd>
      </div>
    {/each}
  </dl>

  {#if explain}
    <p class="note">{UNKNOWN_COUNTS_AS_NO}</p>
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
  .table {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 0;
  }
  .ability {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 26px;
    font-size: 12px;
  }
  .label {
    width: 110px;
    flex-shrink: 0;
    color: var(--c-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .answer {
    width: 64px;
    flex-shrink: 0;
    margin: 0;
    font-weight: 600;
  }
  .ability[data-tone='yes'] .answer {
    color: var(--c-accent);
  }
  .ability[data-tone='no'] .answer {
    color: var(--c-ink-2);
  }
  .ability[data-tone='unknown'] .answer {
    color: var(--c-warn);
  }
  .from {
    margin: 0;
    color: var(--c-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .note {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--c-warn);
  }
</style>
