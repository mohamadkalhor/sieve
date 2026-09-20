<script lang="ts">
  /**
   * "Where 0.88 comes from" (CONSOLE.md section 6.7).
   *
   * The bars are the row's *raw* score, axis by axis, at the height of the
   * proposed weights beside them, so the heading over them is the raw score.
   * The line under them is the equation that turned it into the number the
   * table compares -- because a row can lead on the axes and still sit lower,
   * and blaming an axis for a gap health made is the mistake finding 11 was
   * about.
   */
  import type { Listed } from '$lib/api/client';
  import { versusLeader, versusText, whyRows } from '../logic/explain';
  import { barWidth, fitLine, NO_AXES, oneDecimal, points, whyHeading } from './view';

  interface Props {
    row: Listed;
    /** the seat's proposed weights, in the order they were added */
    weights: Record<string, number>;
    order: readonly string[];
    labels: Record<string, string>;
    /** the row at the top of the list, for the comparison sentence */
    leader: Listed | null;
  }

  let { row, weights, order, labels, leader }: Props = $props();

  const rows = $derived(whyRows(row, weights, order, labels));
  const fit = $derived(fitLine(row));
  const sentence = $derived(versusText(versusLeader(row, leader, labels)));
</script>

<section class="block" data-block="why">
  <div class="head">
    <h3>{whyHeading(row.raw ?? row.score)}</h3>
    <span class="unit">points of 100</span>
  </div>

  {#if rows === null}
    <p class="quiet">{NO_AXES}</p>
  {:else}
    <ul class="bars">
      {#each rows as entry (entry.axis)}
        <li class="why" data-axis={entry.axis} data-measured={entry.measured ? 'true' : 'false'}>
          <span class="label">{entry.label}</span>
          <span class="track" data-measured={entry.measured ? 'true' : 'false'}>
            <span
              class="fill"
              style="width: {barWidth(entry.share)}; background: {entry.color}"
            ></span>
          </span>
          {#if entry.measured}
            <span class="num">{oneDecimal(entry.got)} / {points(entry.of)}</span>
          {:else}
            <span class="not">not measured</span>
          {/if}
        </li>
      {/each}
    </ul>

    {#if fit}
      <p class="fit">{fit}</p>
    {/if}
    {#if sentence}
      <p class="versus">{sentence}</p>
    {/if}
  {/if}
</section>

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  h3 {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--c-ink);
  }
  .unit {
    margin-left: auto;
    font-size: 12px;
    color: var(--c-muted);
  }
  .bars {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .why {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 22px;
  }
  .label {
    width: 96px;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--c-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .track {
    flex-grow: 1;
    height: 8px;
    border-radius: var(--r-2);
    background: var(--c-raised);
    overflow: hidden;
    display: flex;
  }
  .fill {
    height: 100%;
  }
  /* An axis nobody measured is hatched, not drawn empty: a zero-length bar
     would read as "scored nothing", which is a different fact. */
  .track[data-measured='false'] {
    background-image: repeating-linear-gradient(
      45deg,
      var(--c-rule-strong) 0 2px,
      transparent 2px 4px
    );
  }
  .num,
  .not {
    min-width: 72px;
    flex-shrink: 0;
    text-align: right;
    font-family: var(--f-mono);
    font-size: 12px;
  }
  .not {
    font-family: var(--f-ui);
    font-size: 11px;
    color: var(--c-muted);
  }
  .fit,
  .versus,
  .quiet {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--c-muted);
  }
  .fit {
    font-family: var(--f-mono);
  }
</style>
