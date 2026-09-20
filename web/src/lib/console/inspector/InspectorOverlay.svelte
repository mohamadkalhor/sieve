<script lang="ts">
  /**
   * The inspector when it is not a pane of its own (CONSOLE.md section 6.8,
   * REVIEW.md finding 13): a 340px drawer over the right edge of the seat pane
   * between 1024 and 1279, and a bottom sheet -- at most 80dvh -- below 900.
   *
   * It is a dialog, and says so. The point of building it as one is that it
   * covers part of the seat: focus moves into the panel when it opens, Tab
   * stays inside it, Escape and the close button close it, and focus goes back
   * to what had it -- the row that was clicked, usually -- so a keyboard reader
   * is never dropped at the top of the page by opening a model and never
   * stranded in a panel they cannot see past.
   *
   * The route decides whether this is drawn at all: it is only mounted below
   * 1280px, and only when a selection was *asked for* rather than adopted (see
   * `state/selection.svelte.ts`), because a panel that opened itself over the
   * seat every time the seat answered would make the seat unreadable.
   */
  import { tick } from 'svelte';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Icon from '../ui/Icon.svelte';
  import IconButton from '../ui/IconButton.svelte';
  import HistoryDrawer from '../seat/HistoryDrawer.svelte';
  import Inspector from './Inspector.svelte';

  interface Props {
    session: SeatSession;
    /** where the seat pane ends: `drawer` on its right edge, `sheet` under it */
    kind: 'drawer' | 'sheet';
    onclose: () => void;
  }

  let { session, kind, onclose }: Props = $props();

  let panel = $state<HTMLElement | null>(null);

  /** Everything Tab can stop on, in the order it stops there. */
  const STOPS =
    'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

  $effect(() => {
    const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    void tick().then(() => panel?.focus());

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        // Capture, and stop it here: Escape belongs to the panel on top, and
        // letting it through would also reach whatever the shell listens for.
        event.preventDefault();
        event.stopPropagation();
        onclose();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const stops = Array.from(panel.querySelectorAll<HTMLElement>(STOPS));
      if (stops.length === 0) return;
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    // capture: the panel is on top, so it hears Escape before the page does
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      if (back?.isConnected) back.focus();
    };
  });
</script>

<!-- the dimmed seat pane: the panel covers part of it and takes the focus -->
<div class="scrim" role="presentation" onclick={onclose}></div>

<div
  class="panel"
  data-kind={kind}
  role="dialog"
  aria-modal="true"
  aria-label="Inspector"
  tabindex="-1"
  bind:this={panel}
>
  <header class="top">
    <p class="title">Inspector</p>
    <IconButton label="Close the inspector" onclick={onclose}>
      <Icon name="x" size={12} />
    </IconButton>
  </header>

  <div class="body">
    {#if session.historyOpen}
      <HistoryDrawer {session} />
    {:else}
      <div class="scroll">
        <Inspector {session} />
      </div>
    {/if}
  </div>
</div>

<style>
  /* the whole seat pane, dimmed: the panel covers part of it and steals focus */
  .scrim {
    position: absolute;
    inset: 0;
    z-index: 29;
    background: rgb(0 0 0 / 45%);
  }

  .panel {
    position: absolute;
    z-index: 30;
    display: flex;
    flex-direction: column;
    min-height: 0;
    border: 0;
    background: var(--c-pane);
    box-shadow: 0 12px 40px rgb(0 0 0 / 45%);
  }

  .panel:focus {
    outline: none;
  }

  /* over the seat pane's right edge */
  .panel[data-kind='drawer'] {
    top: 0;
    right: 0;
    bottom: 0;
    width: min(340px, 100%);
    border-left: 1px solid var(--c-rule);
  }

  /* under the seat pane */
  .panel[data-kind='sheet'] {
    right: 0;
    bottom: 0;
    left: 0;
    max-height: min(80dvh, 100%);
    border-top: 1px solid var(--c-rule);
    border-radius: var(--r-3) var(--r-3) 0 0;
  }

  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex: 0 0 auto;
    padding: 10px 12px;
    border-bottom: 1px solid var(--c-rule);
  }

  .title {
    margin: 0;
    font: 500 11px/1 var(--f-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
  }

  .body {
    flex: 1 1 auto;
    min-height: 0;
    overflow: auto;
  }

  .scroll {
    padding: 16px;
  }

  /* the close button is the one thing a thumb has to hit in a dark sheet */
  @media (pointer: coarse) {
    .top {
      padding: 8px 8px 8px 12px;
    }

    .top :global(.ib) {
      width: 44px;
      height: 44px;
    }
  }
</style>
