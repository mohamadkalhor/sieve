<script lang="ts">
  /**
   * Auto/Manual, and every other either-or in the console (CONSOLE.md section
   * 3.3). A radiogroup rather than two buttons: the state is one of two values,
   * not two independent switches, and a screen reader should hear it that way.
   * Arrow keys move the choice, as a radiogroup is expected to.
   */
  interface Props {
    options: { value: string; label: string }[];
    value: string;
    onchange: (value: string) => void;
    label: string;
    class?: string;
  }

  let { options, value, onchange, label, class: klass = '' }: Props = $props();

  let buttons = $state<HTMLButtonElement[]>([]);

  function move(from: number, step: number): void {
    const next = (from + step + options.length) % options.length;
    onchange(options[next].value);
    buttons[next]?.focus();
  }
</script>

<div class="seg {klass}" role="radiogroup" aria-label={label}>
  {#each options as option, i (option.value)}
    <button
      type="button"
      role="radio"
      aria-checked={value === option.value}
      tabindex={value === option.value ? 0 : -1}
      bind:this={buttons[i]}
      onclick={() => onchange(option.value)}
      onkeydown={(event) => {
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
          event.preventDefault();
          move(i, 1);
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          event.preventDefault();
          move(i, -1);
        }
      }}
    >
      {option.label}
    </button>
  {/each}
</div>

<style>
  .seg {
    display: inline-flex;
    padding: 2px;
    background: var(--c-panel);
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-3);
  }
  button {
    height: 30px;
    padding: 0 12px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-muted);
    font-family: var(--f-ui);
    font-size: 13px;
    cursor: pointer;
  }
  button[aria-checked='true'] {
    background: var(--c-rule-strong);
    color: var(--c-ink);
  }

  /* §6.8: below 900px the two halves are 44px targets. */
  @media (max-width: 899px) {
    button {
      height: 44px;
    }
  }
</style>
