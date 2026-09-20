<script lang="ts">
  /**
   * A button whose whole content is an icon, so it has no text to be named by:
   * `label` is required and becomes the `aria-label` (CONSOLE.md section 3.3).
   * `pressed` turns it into a toggle and reports the state, not just the look.
   */
  import type { Snippet } from 'svelte';
  import type { HTMLButtonAttributes } from 'svelte/elements';

  interface Props extends HTMLButtonAttributes {
    label: string;
    pressed?: boolean | undefined;
    size?: 28 | 30 | 44;
    class?: string;
    children?: Snippet;
  }

  let {
    label,
    pressed = undefined,
    size = 28,
    title = undefined,
    onclick = undefined,
    class: klass = '',
    children
  }: Props = $props();
</script>

<button
  type="button"
  class="ib {klass}"
  style="--ib: {size}px"
  aria-label={label}
  title={title ?? label}
  aria-pressed={pressed}
  {onclick}
>
  {@render children?.()}
</button>

<style>
  .ib {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--ib);
    height: var(--ib);
    padding: 0;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink-2);
    cursor: pointer;
  }
  .ib:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }
  .ib[aria-pressed='true'] {
    color: var(--c-accent);
  }

  /* §6.8: below 900px an icon button is a 44px target. A minimum rather than a
     width, so a button that was asked for a bigger size keeps it. */
  @media (max-width: 899px) {
    .ib {
      min-width: 44px;
      min-height: 44px;
    }
  }
</style>
