<script lang="ts">
  /**
   * The effort a row's score is about (EFFORT.md section 6, mockup section 2).
   *
   * Four looks, so a stand-in never reads as the real thing: the seat's own
   * effort in the accent, a nearest one dashed and amber, an effort the router
   * id names in the cool colour, and "one setting" muted. An "any" seat draws
   * the matched row's effort as plain muted text. The words and the hover come
   * from `effortPill` in `rows.ts`; this file only paints them.
   */
  import type { Effort, Listed } from '$lib/api/client';
  import { effortPill } from './rows';

  interface Props {
    row: Pick<Listed, 'name' | 'local_ids' | 'effort' | 'effort_how'>;
    /** the effort the seat runs at, for the stand-in's sentence */
    seat?: Effort | null;
  }

  let { row, seat = null }: Props = $props();

  const pill = $derived(effortPill(row, seat));
</script>

{#if pill}
  <span class="pill" data-look={pill.look} data-effort-pill title={pill.title}>{pill.text}</span>
{/if}

<style>
  .pill {
    flex: none;
    padding: 2px 6px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-pill);
    font-family: var(--f-mono);
    font-size: 10.5px;
    font-weight: 500;
    line-height: 1.2;
    color: var(--c-ink-2);
    white-space: nowrap;
  }

  .pill[data-look='exact'] {
    border-color: color-mix(in srgb, var(--c-accent) 35%, var(--c-rule-strong));
    color: var(--c-accent);
  }

  /* a stand-in: dashed, so it never reads as the seat's own effort */
  .pill[data-look='near'] {
    border-style: dashed;
    border-color: var(--c-warn);
    color: var(--c-warn);
  }

  .pill[data-look='id'] {
    border-color: color-mix(in srgb, var(--c-axis-1) 35%, var(--c-rule-strong));
    color: var(--c-axis-1);
  }

  .pill[data-look='one'] {
    color: var(--c-muted);
  }

  /* "any": the matched row's effort, said without a frame */
  .pill[data-look='any'] {
    border-color: transparent;
    padding-inline: 0;
    color: var(--c-muted);
  }
</style>
