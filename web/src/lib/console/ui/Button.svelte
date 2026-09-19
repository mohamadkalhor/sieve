<script lang="ts">
  /**
   * The one button (CONSOLE.md section 3.3).
   *
   * `md` is 36px -- the shell's own controls. `sm` is 28px, the height a row
   * action or a chip wants. The inspector footer passes its own height through
   * `class`, which is why the height lives in a custom property rather than
   * being nailed to the variant.
   */
  import type { Snippet } from 'svelte';
  import type { HTMLButtonAttributes } from 'svelte/elements';

  interface Props extends HTMLButtonAttributes {
    variant?: 'primary' | 'outline' | 'ghost' | 'danger';
    size?: 'sm' | 'md';
    class?: string;
    children?: Snippet;
  }

  let {
    variant = 'outline',
    size = 'md',
    type = 'button',
    disabled = false,
    title = undefined,
    onclick = undefined,
    class: klass = '',
    children
  }: Props = $props();
</script>

<button
  {type}
  {disabled}
  {title}
  {onclick}
  class="btn {klass}"
  data-variant={variant}
  data-size={size}
>
  {@render children?.()}
</button>

<style>
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    height: var(--btn-h, 36px);
    padding: 0 var(--btn-pad, 12px);
    border: 1px solid transparent;
    border-radius: var(--r-3);
    font-family: var(--f-ui);
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    cursor: pointer;
    background: transparent;
    color: var(--c-ink);
    transition: background-color 120ms ease, border-color 120ms ease;
  }
  .btn[data-size='sm'] {
    --btn-h: 28px;
    --btn-pad: 10px;
  }
  .btn[data-variant='primary'] {
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font-weight: 600;
  }
  .btn[data-variant='primary']:hover:not(:disabled) {
    background: color-mix(in srgb, var(--c-accent) 88%, white);
  }
  .btn[data-variant='outline'] {
    border-color: var(--c-rule-hover);
    background: var(--c-panel);
  }
  .btn[data-variant='outline']:hover:not(:disabled) {
    background: var(--c-raised);
  }
  .btn[data-variant='ghost'] {
    color: var(--c-ink-2);
  }
  .btn[data-variant='ghost']:hover:not(:disabled) {
    background: var(--c-raised);
  }
  .btn[data-variant='danger'] {
    color: var(--c-bad);
    border-color: var(--c-rule-strong);
    background: var(--c-panel);
  }
  .btn[data-variant='danger']:hover:not(:disabled) {
    border-color: var(--c-bad);
  }
  /* A disabled button must not colour like a live one: it used to look ready. */
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
