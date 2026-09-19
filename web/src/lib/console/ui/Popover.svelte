<script lang="ts">
  /**
   * A panel under the thing that opened it (CONSOLE.md section 3.3).
   *
   * No portal library: a fixed-position div, clamped to the viewport, is all
   * this needs. Esc and a click outside close it, and focus moves in on open
   * and back to the anchor on close -- a popover that steals focus and never
   * gives it back strands a keyboard reader at the top of the page.
   */
  import { tick } from 'svelte';
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    anchor: HTMLElement | null;
    onclose: () => void;
    label: string;
    width?: number;
    children?: Snippet;
  }

  let { open, anchor, onclose, label, width = 280, children }: Props = $props();

  let panel = $state<HTMLElement | null>(null);
  let spot = $state({ top: 0, left: 0 });

  const MARGIN = 8;

  /** Under the anchor, clamped so a panel near an edge stays on screen. */
  function place(): void {
    if (!anchor) return;
    const box = anchor.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - MARGIN * 2);
    const left = Math.max(MARGIN, Math.min(box.left, window.innerWidth - w - MARGIN));
    const below = box.bottom + 4;
    const height = panel?.offsetHeight ?? 0;
    // flip above only when there is genuinely no room below and more above
    const top = below + height > window.innerHeight - MARGIN && box.top - 4 - height > MARGIN
      ? box.top - 4 - height
      : below;
    spot = { top, left };
  }

  function focusables(): HTMLElement[] {
    if (!panel) return [];
    return Array.from(
      panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  $effect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    void tick().then(() => {
      place();
      focusables()[0]?.focus();
    });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onclose();
      }
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (panel?.contains(target) || anchor?.contains(target)) return;
      onclose();
    };
    const onScroll = () => place();
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('mousedown', onDown, true);
    window.addEventListener('resize', onScroll);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('mousedown', onDown, true);
      window.removeEventListener('resize', onScroll);
      window.removeEventListener('scroll', onScroll, true);
      const back = anchor ?? previous;
      if (back && document.contains(back)) back.focus();
    };
  });
</script>

{#if open}
  <div
    class="pop"
    bind:this={panel}
    role="dialog"
    aria-label={label}
    style="top: {spot.top}px; left: {spot.left}px; width: {Math.min(width, 480)}px"
  >
    {@render children?.()}
  </div>
{/if}

<style>
  .pop {
    position: fixed;
    z-index: 60;
    max-height: calc(100dvh - 16px);
    overflow: auto;
    padding: 8px;
    background: var(--c-panel);
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-3);
    box-shadow: 0 12px 32px rgb(0 0 0 / 0.45);
    font-size: 13px;
  }
</style>
