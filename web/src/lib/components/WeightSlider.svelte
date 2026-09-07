<script lang="ts">
  interface Props {
    axis: string;
    label?: string;
    value: number;
    locked?: boolean;
    onchange: (value: number) => void;
    onlock?: (locked: boolean) => void;
  }
  let { axis, label = '', value, locked = false, onchange, onlock }: Props = $props();

  const id = $derived(`w-${axis}`);
  const shown = $derived(value.toFixed(2));
</script>

<div class="slider" class:locked>
  <label for={id}>{label || axis.replace(/_/g, ' ')}</label>
  <input
    {id}
    type="range"
    min="0"
    max="1"
    step="0.01"
    value={value}
    disabled={locked}
    aria-valuetext={`${shown} of 1`}
    oninput={(event) => onchange(Number((event.currentTarget as HTMLInputElement).value))}
  />
  <output class="num" for={id}>{shown}</output>
  {#if onlock}
    <button
      type="button"
      class="lock"
      aria-pressed={locked}
      aria-label={locked ? `unlock ${axis}` : `lock ${axis}`}
      onclick={() => onlock?.(!locked)}
    >
      {locked ? 'locked' : 'lock'}
    </button>
  {/if}
</div>

<style>
  .slider {
    display: grid;
    grid-template-columns: 8.5rem 1fr 3rem auto;
    gap: 0.6rem;
    align-items: center;
    padding: 0.2rem 0;
  }
  label {
    color: var(--muted);
    font-size: 0.8rem;
    text-transform: capitalize;
    overflow-wrap: anywhere;
  }
  input {
    accent-color: var(--accent);
    width: 100%;
    min-width: 4rem;
  }
  output {
    text-align: right;
    font-size: 0.8rem;
  }
  .lock {
    background: none;
    border: 1px solid var(--rule);
    border-radius: 999px;
    color: var(--muted);
    font: inherit;
    font-size: 0.68rem;
    padding: 0.05rem 0.45rem;
    cursor: pointer;
  }
  .slider.locked .lock {
    color: var(--accent);
    border-color: var(--accent);
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
