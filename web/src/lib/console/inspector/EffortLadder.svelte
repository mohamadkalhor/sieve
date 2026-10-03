<script lang="ts">
  /**
   * The effort ladder, first block of the inspector (EFFORT.md section 6,
   * mockup callout 3).
   *
   * Every effort the model's family publishes, highest first, each with its
   * score on this seat and its intelligence index; a dot says whether a router
   * serves that row. The row the seat's score used carries the accent edge,
   * and an effort the family does not publish is still listed, greyed, so a
   * stand-in is visibly a stand-in. The sentence under it says the same thing
   * in words. Nothing here is drawn for a one-setting model: the card's ladder
   * is empty and the parent skips the block. The link at the foot opens the
   * Field from this seat, where the seat's effort is ringed on each line.
   */
  import type { Effort, EffortHow, LadderRung } from '$lib/api/client';
  import { ladderLines, ladderSentence, SAME_PRICE } from './view';

  interface Props {
    ladder: LadderRung[];
    /** the effort the seat runs at; null = any */
    seat: Effort | null;
    seatName: string;
    name: string;
    how?: EffortHow | null;
  }

  let { ladder, seat, seatName, name, how = null }: Props = $props();

  const lines = $derived(ladderLines(ladder, seat));
  const sentence = $derived(ladderSentence(ladder, seat, name, how));
</script>

<section class="block" data-block="ladder">
  <div class="head">
    <h3>Effort ladder</h3>
    <span class="unit">score on {seatName} · intelligence</span>
  </div>

  <ul class="rungs">
    {#each lines as line (line.effort)}
      <li
        class="rung"
        data-effort={line.effort}
        data-here={line.here ? 'true' : undefined}
        data-published={line.published ? 'true' : 'false'}
        title={line.title}
      >
        <span class="word">
          <span class="dot" data-on={line.reachable ? 'true' : 'false'}></span>
          {line.word}
        </span>
        {#if line.width !== null}
          <span class="track"><span class="fill" style="width: {line.width}"></span></span>
        {:else}
          <span class="note">{line.note}</span>
        {/if}
        <span class="num">{line.score}</span>
        <span class="num iq">{line.intelligence}</span>
      </li>
    {/each}
  </ul>

  {#if sentence}<p class="say">{sentence}</p>{/if}
  <p class="say">{SAME_PRICE}</p>
  <a class="field" href={`/field?seat=${encodeURIComponent(seatName)}`}>See it on the Field</a>
</section>

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  h3 {
    flex: 1 1 auto;
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--c-ink);
  }

  .unit {
    font-size: 11px;
    color: var(--c-muted);
  }

  .rungs {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .rung {
    display: grid;
    grid-template-columns: 86px minmax(0, 1fr) 38px 44px;
    align-items: center;
    gap: 8px;
    min-height: 26px;
    padding: 0 6px;
    border-radius: var(--r-2);
    font-size: 12px;
  }

  .rung[data-here='true'] {
    background: var(--c-raised);
    box-shadow: inset 2px 0 0 var(--c-accent);
  }

  .word {
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--f-mono);
    color: var(--c-ink-2);
  }

  .rung[data-published='false'] .word {
    color: var(--c-muted);
  }

  .dot {
    flex: none;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--c-accent);
  }

  .dot[data-on='false'] {
    background: transparent;
    border: 1px solid var(--c-rule-hover);
  }

  .track {
    height: 7px;
    overflow: hidden;
    border-radius: var(--r-2);
    background: var(--c-raised);
  }

  .rung[data-here='true'] .track {
    background: var(--c-rule-strong);
  }

  .fill {
    display: block;
    height: 100%;
    background: var(--c-dim);
  }

  .rung[data-here='true'] .fill {
    background: var(--c-accent);
  }

  .note {
    overflow: hidden;
    font-size: 11px;
    color: var(--c-muted);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .num {
    font-family: var(--f-mono);
    font-variant-numeric: tabular-nums;
    text-align: right;
    color: var(--c-ink-2);
  }

  .iq {
    color: var(--c-muted);
  }

  .field {
    align-self: flex-start;
    font-size: 12px;
    color: var(--c-ink-2);
    text-underline-offset: 2px;
  }

  .field:hover {
    color: var(--c-accent);
  }

  .say {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--c-muted);
  }
</style>
