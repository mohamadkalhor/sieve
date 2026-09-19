<script lang="ts">
  /**
   * The weights, auto mode only (CONSOLE.md section 6.4).
   *
   * Row one is the words and the presets; row two is the bar. When the pane is
   * too narrow for the bar to be honest -- `fits` says a stub would be drawn
   * under its own minimum width -- the bar is replaced by one row per axis with
   * the exact percentage, the lock, and the way to remove it. The bar is never
   * drawn as a lie and the numbers are never out of reach.
   */
  import { COST_AXIS, PRESETS, type Preset } from '$lib/profile/tune';
  import { dropAxis, preset, setExact, toggleLock, type Settings } from '$lib/console/logic/settings';
  import { fits, layout, trackAxes } from '$lib/console/logic/split';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Button from '$lib/console/ui/Button.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import IconButton from '$lib/console/ui/IconButton.svelte';
  import SplitBar from './SplitBar.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let row = $state<HTMLElement | null>(null);
  let rowPx = $state(0);

  // The Add button sits in the same row as the bar, so the width the bar gets
  // is the row's width less everything else in it.
  const ADD_PX = 48;

  $effect(() => {
    const element = row;
    if (!element) return;
    rowPx = element.clientWidth;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (width > 0) rowPx = width;
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  });

  const settings = $derived(session.settings);
  const weights = $derived(settings?.weights ?? {});
  const order = $derived(settings?.order ?? []);
  const axes = $derived(trackAxes(weights, order));
  const barPx = $derived(rowPx > 0 ? rowPx - (session.spare.length ? ADD_PX : 0) : 0);
  const room = $derived(barPx > 0 && fits(weights, order, barPx));
  const held = $derived(settings?.locked ?? []);

  function pick(kind: Preset): void {
    if (settings) session.edit(preset(settings, kind));
  }

  /** Every axis, for the row-per-axis fallback the bar gives way to. */
  const exact = $derived(trackAxes(weights, order));
</script>

<div class="block">
  <div class="head">
    <span class="cap">Weights</span>
    <span class="hint">one bar, drag a divider</span>
    <div class="presets">
      {#each PRESETS as item (item.id)}
        <button
          type="button"
          class="chip"
          title={item.says}
          disabled={item.id !== 'even' && !(COST_AXIS in weights)}
          onclick={() => pick(item.id)}
          >{item.label}</button
        >
      {/each}
      <button
        type="button"
        class="chip"
        title="the weights this page opened with"
        onclick={() => session.resetWeights()}
        >Reset</button
      >
    </div>
  </div>

  {#if settings}
    <div class="bar" bind:this={row}>
      {#if room || rowPx === 0}
        <SplitBar
          {settings}
          spare={session.spare}
          labels={session.labels}
          meanings={session.meanings}
          onedit={session.edit}
        />
      {:else}
        <ul class="exact">
          {#each exact as axis (axis)}
            {@const own = weights[axis] ?? 0}
            {@const isLocked = held.includes(axis)}
            {@const field = `inline-${axis.replace(/[^a-zA-Z0-9]+/g, '-')}`}
            <li>
              <span class="name" title={session.meanings[axis] ?? ''}>{session.labels[axis]}</span>
              <label class="field" for={field}>Percent</label>
              <input
                id={field}
                class="num"
                type="number"
                min="0"
                max="100"
                step="1"
                value={Math.round(own * 100)}
                disabled={isLocked}
                onchange={(event) => session.edit(setExact(settings, axis, event.currentTarget.value))}
              />
              <IconButton
                label={isLocked ? `Unlock ${session.labels[axis]}` : `Lock ${session.labels[axis]}`}
                pressed={isLocked}
                onclick={() => session.edit(toggleLock(settings, axis))}
              >
                <Icon name="lock" filled={isLocked} size={14} />
              </IconButton>
              <IconButton
                label={`Remove ${session.labels[axis]}`}
                onclick={() => session.edit(dropAxis(settings, axis))}
              >
                <Icon name="x" size={12} />
              </IconButton>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</div>

<style>
  .block {
    padding: 0 20px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .head {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .cap {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
  }

  .hint {
    font-size: 11px;
    color: var(--c-muted);
  }

  .presets {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .chip {
    height: 26px;
    padding: 0 9px;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-pill);
    background: transparent;
    color: var(--c-ink-2);
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }

  .chip:hover:not(:disabled) {
    border-color: var(--c-rule-hover);
    color: var(--c-ink);
  }

  .chip:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .exact {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .exact li {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .name {
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: var(--c-ink-2);
  }

  .field {
    font-size: 11px;
    color: var(--c-muted);
  }

  .num {
    width: 72px;
    padding: 3px 6px;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: var(--c-pane);
    color: var(--c-ink);
    font-family: var(--f-mono);
    font-size: 12px;
    text-align: right;
  }
</style>
