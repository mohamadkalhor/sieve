<script lang="ts">
  /**
   * The weight bar (CONSOLE.md section 6.4).
   *
   * Pixels come from `logic/split.ts`'s pure `layout`, so the picture and the
   * arithmetic that decides what the numbers mean are the same arithmetic; the
   * component only measures the bar and turns pointer and key events into an
   * edit. Two rules the brief is explicit about are enforced by that split:
   * the bar is drawn from `pxToShare`-style proportional widths rather than
   * snap points, and it is drawn from `layout` rather than measured back out of
   * the DOM, so a drag can pass a segment 0px wide without the bar collapsing
   * to whatever the browser laid out last.
   *
   * A drag rewrites the layout from the segment sizes measured at pointerdown
   * (the same figures the edit is computed from) rather than from
   * `getBoundingClientRect()` on every move, so the segment under the pointer
   * matches the number the pointer is producing.
   *
   * Hidden axes are not drawn at all, which is `layout`'s job; this component
   * never filters them itself.
   */
  import type { AxisRow } from '$lib/api/client';
  import {
    GAP_PX,
    KEY_STEP,
    KEY_STEP_BIG,
    fits,
    label,
    layout,
    parties,
    pxToShare,
    type Segment
  } from '$lib/console/logic/split';
  import { addAxis, transferWeight, type Settings } from '$lib/console/logic/settings';
  import Icon from '$lib/console/ui/Icon.svelte';
  import AddAxisPopover from './AddAxisPopover.svelte';
  import AxisPopover from './AxisPopover.svelte';

  interface Props {
    settings: Settings;
    spare: AxisRow[];
    labels: Record<string, string>;
    meanings: Record<string, string>;
    onedit: (next: Settings | { error: string }) => void;
  }

  let { settings, spare, labels, meanings, onedit }: Props = $props();

  let track = $state<HTMLElement | null>(null);
  let barPx = $state(0);

  /**
   * The bar's width, from the element itself.
   *
   * The first measurement is taken synchronously (a resize observer only
   * reports *changes*), and the observer keeps it honest from then on: a
   * sidebar that opens must re-draw the bar, not leave it at the old width.
   */
  $effect(() => {
    const element = track;
    if (!element) return;
    barPx = element.clientWidth;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const width = entry.contentRect.width;
        if (width > 0) barPx = width;
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  });

  const segments = $derived(layout(settings.weights, settings.order, barPx));
  const room = $derived(fits(settings.weights, settings.order, barPx));
  /** One divider per boundary: the gap before segment `index`, for `index` >= 1. */
  const dividers = $derived(segments.map((_before, index) => index).slice(1));

  let openAxis = $state<{ axis: string; element: HTMLElement } | null>(null);
  let addOpen = $state(false);
  let addAnchor = $state<HTMLElement | null>(null);

  interface Drag {
    at: number;
    left: string;
    right: string;
    from: number;
    start: Settings;
    startSegments: Segment[];
    bar: number;
  }

  let drag = $state<Drag | null>(null);
  let pendingX = 0;
  let frame = 0;

  /** The centre of the gap before `index`, in track coordinates. */
  function centre(index: number): number {
    let x = 0;
    for (let i = 0; i < index; i += 1) x += segments[i].px + GAP_PX;
    return x - GAP_PX / 2;
  }

  function down(event: PointerEvent, at: number): void {
    const pair = parties(segments, settings.locked, at);
    if (!pair) return;
    event.preventDefault();
    const element = event.currentTarget as HTMLElement;
    element.setPointerCapture(event.pointerId);
    drag = {
      at,
      left: pair[0],
      right: pair[1],
      from: event.clientX,
      start: settings,
      startSegments: [...segments],
      bar: barPx
    };
    pendingX = event.clientX;
    frame = requestAnimationFrame(applyDrag);
  }

  /**
   * Every move is computed from the pointerdown state, not from the last move:
   * clamping inside `transfer` would otherwise compound and the segment would
   * lag behind the pointer once it hits a limit.
   */
  function applyDrag(): void {
    frame = 0;
    const current = drag;
    if (!current) return;
    const delta = pxToShare(pendingX - current.from, current.startSegments, current.bar);
    onedit(transferWeight(current.start, current.left, current.right, delta));
  }

  function move(event: PointerEvent): void {
    if (!drag) return;
    pendingX = event.clientX;
    if (!frame) frame = requestAnimationFrame(applyDrag);
  }

  function up(event: PointerEvent): void {
    if (!drag) return;
    const element = event.currentTarget as HTMLElement;
    if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    // A click that never moved is not a drag; the last frame is flushed anyway
    // so a one-pixel nudge lands.
    applyDrag();
    drag = null;
  }

  function key(event: KeyboardEvent, at: number): void {
    const pair = parties(segments, settings.locked, at);
    if (!pair) return;
    const [left, right] = pair;
    const step = event.shiftKey ? KEY_STEP_BIG : KEY_STEP;
    let delta: number;
    switch (event.key) {
      case 'ArrowLeft':
        delta = -step;
        break;
      case 'ArrowRight':
        delta = step;
        break;
      case 'Home':
        delta = settings.weights[right] ?? 0;
        break;
      case 'End':
        delta = -(settings.weights[left] ?? 0);
        break;
      default:
        return;
    }
    event.preventDefault();
    onedit(transferWeight(settings, left, right, delta));
  }

  function number(index: number, segment: Segment): string {
    const drawn = label(segment, labels[segment.axis]).number;
    if (drawn) return drawn;
    // While a drag is running, the two axes being traded show their numbers
    // even when narrow: the picture would otherwise say nothing at all about
    // the thing the hand is doing.
    if (drag && (index === drag.at - 1 || index === drag.at)) {
      return String(Math.round(segment.weight * 100));
    }
    return '';
  }
</script>

<div class="row">
  <div class="track" bind:this={track} style="gap: {GAP_PX}px">
    {#each segments as segment, index (segment.axis)}
      {@const shown = label(segment, labels[segment.axis])}
      {@const isLocked = settings.locked.includes(segment.axis)}
      <button
        type="button"
        class="seg"
        class:narrow={!shown.text && !shown.number}
        style="width: {segment.px}px; background: {segment.color}"
        aria-label={`${labels[segment.axis]}, ${Math.round(segment.weight * 100)} percent${
          isLocked ? ', locked' : ''
        }`}
        onclick={(event) => (openAxis = { axis: segment.axis, element: event.currentTarget })}
      >
        {#if shown.text}<span class="name">{shown.text}</span>{/if}
        <span class="tail">
          {#if isLocked}<Icon name="lock" filled size={10} />{/if}
          <span class="num">{number(index, segment)}</span>
        </span>
      </button>
    {/each}

    {#each dividers as index (index)}
      {@const pair = parties(segments, settings.locked, index)}
      {@const leftShare = segments[index - 1]?.weight ?? 0}
      {@const pairShare = leftShare + (segments[index]?.weight ?? 0)}
      {#if pair}
        <button
          type="button"
          class="divider"
          role="slider"
          aria-orientation="vertical"
          aria-valuemin={0}
          aria-valuemax={Math.round(pairShare * 100)}
          aria-valuenow={Math.round(leftShare * 100)}
          aria-label={`Between ${labels[pair[0]]} and ${labels[pair[1]]}`}
          aria-valuetext={`${labels[pair[0]]} ${Math.round(leftShare * 100)} of ${Math.round(
            pairShare * 100
          )} percent`}
          style="left: {centre(index)}px"
          onpointerdown={(event) => down(event, index)}
          onpointermove={move}
          onpointerup={up}
          onpointercancel={up}
          onkeydown={(event) => key(event, index)}
        ></button>
      {/if}
    {/each}
  </div>

  {#if spare.length}
    <button
      type="button"
      class="add"
      aria-label="Add an axis"
      title="Add an axis"
      onclick={(event) => {
        addAnchor = event.currentTarget;
        addOpen = !addOpen;
      }}
    >
      <Icon name="plus" size={14} />
    </button>
  {/if}
</div>

{#if openAxis}
  <AxisPopover
    open={true}
    anchor={openAxis.element}
    axis={openAxis.axis}
    label={labels[openAxis.axis]}
    meaning={meanings[openAxis.axis] ?? ''}
    {settings}
    {onedit}
    onclose={() => (openAxis = null)}
  />
{/if}

<AddAxisPopover
  open={addOpen}
  anchor={addAnchor}
  {spare}
  onpick={(axis) => onedit(addAxis(settings, axis))}
  onclose={() => (addOpen = false)}
/>

{#if !room && barPx > 0}
  <p class="hint">
    The bar is too narrow to show every axis: drag the dividers you can see, or use the exact
    percentages.
  </p>
{/if}

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .track {
    position: relative;
    display: flex;
    height: 40px;
    flex: 1 1 auto;
    min-width: 0;
  }

  .seg {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 100%;
    padding: 0 8px;
    border: 0;
    border-radius: 3px;
    color: var(--c-accent-ink);
    font-family: inherit;
    font-size: 12px;
    font-weight: 600;
    overflow: hidden;
    white-space: nowrap;
    cursor: pointer;
    flex: 0 0 auto;
  }

  .name {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tail {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .seg.narrow {
    padding: 0;
    justify-content: center;
  }

  .divider {
    position: absolute;
    top: -4px;
    bottom: -4px;
    width: 11px;
    margin-left: -5.5px;
    cursor: col-resize;
    touch-action: none;
    background: transparent;
    z-index: 1;
  }

  .divider:focus-visible {
    outline: none;
    box-shadow: 0 0 0 2px var(--c-accent);
    border-radius: 2px;
  }

  /* A thumb needs a target it can find; the divider itself is invisible, so the
     wide hit area costs the picture nothing. */
  @media (pointer: coarse) {
    .divider {
      width: 44px;
      margin-left: -22px;
    }
  }

  .add {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    flex: 0 0 auto;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink-2);
    cursor: pointer;
  }

  .add:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  @media (max-width: 899px) {
    .track,
    .seg {
      min-height: 48px;
    }

    .add {
      width: 48px;
      height: 48px;
    }
  }

  .hint {
    margin: 8px 0 0;
    font-size: 11px;
    line-height: 1.4;
    color: var(--c-muted);
  }
</style>
