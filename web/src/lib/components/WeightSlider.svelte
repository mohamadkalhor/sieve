<script lang="ts">
  /**
   * One weight: a slider you can also type into.
   *
   * A slider is quick and imprecise, a number box is precise and slow. Double
   * -clicking the value swaps one for the other, because the Profiles screen
   * carries several of these per row and a permanent number box beside every
   * slider is a wall of digits nobody reads.
   *
   * `idPrefix` exists for the same reason: every profile's sliders are on the
   * page at once, and two rows both claiming `id="w-cost"` is invalid HTML and
   * enough to point a label at the wrong control.
   */
  interface Props {
    axis: string;
    label?: string;
    value: number;
    /** the room this weight is allowed to move in, from the settings route */
    min?: number;
    max?: number;
    locked?: boolean;
    disabled?: boolean;
    idPrefix?: string;
    onchange: (value: number) => void;
    onlock?: (locked: boolean) => void;
  }
  let {
    axis,
    label = '',
    value,
    min = 0,
    max = 1,
    locked = false,
    disabled = false,
    idPrefix = 'w',
    onchange,
    onlock
  }: Props = $props();

  const id = $derived(`${idPrefix}-${axis}`);
  const shown = $derived(value.toFixed(2));
  const frozen = $derived(locked || disabled);

  /** while true the value is a number box rather than an output */
  let typing = $state(false);
  let draft = $state('');

  function openTyping() {
    if (frozen) return;
    draft = String(Number(value.toFixed(4)));
    typing = true;
  }

  function commit() {
    typing = false;
    const next = Number(draft);
    if (!Number.isFinite(next)) return;
    onchange(Math.min(max, Math.max(min, next)));
  }

  function keys(event: KeyboardEvent) {
    if (event.key === 'Enter') commit();
    else if (event.key === 'Escape') typing = false;
  }
</script>

<div class="slider" class:locked class:disabled>
  <label for={id}>{label || axis.replace(/_/g, ' ')}</label>
  <input
    {id}
    type="range"
    {min}
    {max}
    step="0.01"
    {value}
    disabled={frozen}
    aria-valuetext={`${shown} of ${max}`}
    oninput={(event) => onchange(Number((event.currentTarget as HTMLInputElement).value))}
  />
  {#if typing}
    <!-- svelte-ignore a11y_autofocus -->
    <input
      class="typed num"
      type="number"
      {min}
      {max}
      step="0.01"
      autofocus
      aria-label={`${axis} exact value`}
      bind:value={draft}
      onblur={commit}
      onkeydown={keys}
    />
  {:else}
    <!--
      A button rather than an `<output>`, so the exact number is reachable by
      keyboard too. Double-clicking it is what the design asks for; a single
      click and Enter do the same thing, because a control that only answers to
      a double-click cannot be operated without a mouse.
    -->
    <button
      type="button"
      class="value num"
      disabled={frozen}
      title="click to type an exact number"
      aria-label={`${axis} is ${shown}; type an exact number`}
      onclick={openTyping}
      ondblclick={openTyping}
    >
      {shown}
    </button>
  {/if}
  {#if onlock}
    <button
      type="button"
      class="lock"
      aria-pressed={locked}
      aria-label={locked ? `unlock ${axis}` : `lock ${axis}`}
      title={locked ? 'this weight holds while others move' : 'hold this weight'}
      onclick={() => onlock?.(!locked)}
    >
      {locked ? '●' : '○'}
    </button>
  {/if}
</div>

<style>
  .slider {
    display: grid;
    grid-template-columns: 7.5rem 1fr 3rem auto;
    gap: 0.5rem;
    align-items: center;
    padding: 0.12rem 0;
  }
  label {
    color: var(--muted);
    font-size: 0.76rem;
    text-transform: capitalize;
    overflow-wrap: anywhere;
  }
  input[type='range'] {
    accent-color: var(--accent);
    width: 100%;
    min-width: 3.5rem;
  }
  .slider.locked input[type='range'] {
    accent-color: var(--muted);
  }
  .value {
    background: none;
    border: 1px solid transparent;
    border-radius: 5px;
    color: var(--ink);
    font: inherit;
    text-align: right;
    font-size: 0.78rem;
    padding: 0.05rem 0.2rem;
    cursor: text;
  }
  .value:hover:not(:disabled) {
    border-color: var(--rule);
  }
  .value:disabled {
    cursor: default;
    color: var(--muted);
  }
  .typed {
    width: 100%;
    min-width: 0;
    text-align: right;
    font-size: 0.78rem;
    padding: 0.1rem 0.2rem;
    border: 1px solid var(--accent);
    border-radius: 5px;
    background: var(--panel2);
    color: var(--ink);
  }
  .lock {
    background: none;
    border: 1px solid var(--rule);
    border-radius: 999px;
    color: var(--muted);
    font: inherit;
    font-size: 0.62rem;
    line-height: 1.4;
    padding: 0.05rem 0.35rem;
    cursor: pointer;
  }
  .slider.locked .lock {
    color: var(--accent);
    border-color: var(--accent);
  }
  .slider.disabled {
    opacity: 0.5;
  }
  @media (max-width: 700px) {
    .slider {
      grid-template-columns: 1fr 3rem auto;
    }
    label {
      grid-column: 1 / -1;
    }
  }
</style>
