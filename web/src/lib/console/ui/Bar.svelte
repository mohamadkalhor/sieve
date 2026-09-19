<script lang="ts">
  /**
   * A share bar (CONSOLE.md section 3.3).
   *
   * `value === null` is not zero: it means the server did not report this
   * number. A zero-width fill would read as "measured, and none", so a null
   * draws a hatched track and no fill at all (rule 2).
   */
  interface Props {
    value: number | null;
    tone?: string;
    title?: string;
    class?: string;
  }

  let { value = null, tone = 'accent', title = '', class: klass = '' }: Props = $props();

  /** 0..1, clamped: a server rounding error must not paint outside the track. */
  const share = $derived(value === null ? 0 : Math.max(0, Math.min(1, value)));
</script>

<div
  class="bar {klass}"
  data-tone={tone}
  data-known={value !== null}
  style="--share: {(share * 100).toFixed(2)}%"
  role="img"
  aria-label={title || (value === null ? 'not reported' : `${Math.round(share * 100)}%`)}
  {title}
>
  <span class="fill"></span>
</div>

<style>
  .bar {
    position: relative;
    height: 6px;
    border-radius: 3px;
    background: var(--c-raised);
    overflow: hidden;
  }
  .fill {
    position: absolute;
    inset: 0 auto 0 0;
    width: var(--share);
    border-radius: 3px;
    background: var(--c-accent);
  }
  .bar[data-tone='dim'] .fill {
    background: var(--c-dim);
  }
  .bar[data-tone='warn'] .fill {
    background: var(--c-warn);
  }
  .bar[data-tone='bad'] .fill {
    background: var(--c-bad);
  }
  /*
   * An axis keeps its own colour as the axis order changes, so the same axis is
   * the same colour in the split bar, its bar and its share.
   */
  .bar[data-tone='axis-0'] .fill { background: var(--c-axis-0); }
  .bar[data-tone='axis-1'] .fill { background: var(--c-axis-1); }
  .bar[data-tone='axis-2'] .fill { background: var(--c-axis-2); }
  .bar[data-tone='axis-3'] .fill { background: var(--c-axis-3); }
  .bar[data-tone='axis-4'] .fill { background: var(--c-axis-4); }
  .bar[data-tone='axis-5'] .fill { background: var(--c-axis-5); }
  .bar[data-tone='axis-6'] .fill { background: var(--c-axis-6); }
  .bar[data-tone='axis-7'] .fill { background: var(--c-axis-7); }
  /* nothing reported: stripes, so it cannot be read as an empty bar */
  .bar[data-known='false'] {
    background: repeating-linear-gradient(45deg, var(--c-raised) 0 4px, var(--c-rule) 4px 8px);
  }
  .bar[data-known='false'] .fill {
    width: 0;
  }
</style>
