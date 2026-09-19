<script lang="ts">
  /**
   * The app shell (CONSOLE.md section 6.1).
   *
   * Three fixed rows: the top bar, one stage that a route fills, and the status
   * bar. The rail is gone -- sections moved into the top bar and the seats
   * route became a three-pane workspace, so nothing is beside the page
   * any more and the stage is the whole width.
   *
   * The seats route is the one exception to the stage's own scrolling: it is
   * three panes that each scroll themselves, and a scrolling stage around them
   * would drag the seat list away from its own filters.
   *
   * There is exactly one window key handler in the console, and it is here.
   * Two handlers that both feel for Ctrl-K would each open the palette and one
   * of them would win by timing.
   */
  import type { Snippet } from 'svelte';
  import { page } from '$app/stores';
  import { palette } from '$lib/console/context';
  import TopBar from './TopBar.svelte';
  import StatusBar from './StatusBar.svelte';
  import CommandPalette from './CommandPalette.svelte';

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();

  const commands = palette();
  const here = $derived($page.url.pathname);
  const flush = $derived(here.startsWith('/seats'));

  /** `/` is a shortcut, not a character, whenever the reader is not typing. */
  function typing(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null;
    if (!el || !el.tagName) return false;
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable;
  }

  function onKey(event: KeyboardEvent): void {
    if (event.key === 'k' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      commands.toggle();
      return;
    }
    if (event.key === '/' && !event.metaKey && !event.ctrlKey && !commands.open && !typing(event.target)) {
      event.preventDefault();
      commands.toggle();
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="shell">
  <TopBar />
  <main class="stage" data-flush={flush}>
    {@render children?.()}
  </main>
  <StatusBar />
</div>

<CommandPalette />

<style>
  .shell {
    display: grid;
    grid-template-rows: var(--h-top) minmax(0, 1fr) var(--h-status);
    height: 100dvh;
  }

  .stage {
    min-height: 0;
    overflow: auto;
    padding: 20px 28px 64px;
  }

  .stage[data-flush='true'] {
    overflow: hidden;
    padding: 0;
  }

  @media (max-width: 900px) {
    .stage {
      padding: 12px 12px 48px;
    }
  }
</style>
