<script lang="ts">
  /**
   * One axis's own controls (CONSOLE.md section 6.4): what it means, the exact
   * percentage, its lock, and the way out.
   *
   * A drag on the bar is a picture; this is the place the number is exact. The
   * field commits on `change`, not on every keystroke, so typing `45` is one
   * edit and not `4` then `45`.
   */
  import { dropAxis, setExact, toggleLock, type Settings } from '$lib/console/logic/settings';
  import Button from '$lib/console/ui/Button.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import IconButton from '$lib/console/ui/IconButton.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';

  interface Props {
    open: boolean;
    anchor: HTMLElement | null;
    axis: string;
    label: string;
    meaning: string;
    settings: Settings;
    onedit: (next: Settings | { error: string }) => void;
    onclose: () => void;
  }

  let { open, anchor, axis, label, meaning, settings, onedit, onclose }: Props = $props();

  const locked = $derived(settings.locked.includes(axis));
  const percent = $derived(Math.round((settings.weights[axis] ?? 0) * 100));
  const field = $derived(`exact-${axis.replace(/[^a-zA-Z0-9]+/g, '-')}`);

  function commitExact(value: string): void {
    onedit(setExact(settings, axis, value));
  }

  function remove(): void {
    // The refusal (the last axis) is a `said`, so the panel closes either way
    // rather than staying open over a bar the person has already left.
    onedit(dropAxis(settings, axis));
    onclose();
  }
</script>

<Popover {open} {anchor} {onclose} label={`${label} axis`} width={264}>
  <div class="body">
    <div class="head">
      <span class="name">{label}</span>
      <IconButton
        label={locked ? `Unlock ${label}` : `Lock ${label}`}
        pressed={locked}
        onclick={() => onedit(toggleLock(settings, axis))}
      >
        <Icon name="lock" filled={locked} size={14} />
      </IconButton>
    </div>

    {#if meaning}
      <p class="meaning">{meaning}</p>
    {/if}

    <div class="row">
      <label for={field}>Percent</label>
      <input
        id={field}
        class="num"
        type="number"
        min="0"
        max="100"
        step="1"
        value={percent}
        disabled={locked}
        onchange={(event) => commitExact(event.currentTarget.value)}
      />
    </div>
    {#if locked}
      <p class="note">Locked: the other axes move around this one.</p>
    {/if}

    <Button variant="danger" size="sm" onclick={remove}>Remove axis</Button>
  </div>
</Popover>

<style>
  .body {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .name {
    font-size: 13px;
    font-weight: 600;
    color: var(--c-ink);
  }

  .meaning {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--c-muted);
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
    color: var(--c-ink-2);
  }

  .num {
    width: 72px;
    padding: 4px 6px;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: var(--c-pane);
    color: var(--c-ink);
    font-family: var(--font-mono);
    font-size: 12px;
    text-align: right;
  }

  .note {
    margin: 0;
    font-size: 11px;
    color: var(--c-muted);
  }
</style>
