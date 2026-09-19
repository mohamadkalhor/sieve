<script lang="ts">
  /**
   * The top bar (CONSOLE.md section 6.1).
   *
   * A 228px logo cell, then the palette trigger, then the sections, then who
   * you are. The trigger is a button the width of the old rail's search box
   * because it is the same job: it is how you reach anything the sections do
   * not name.
   *
   * Below 900px the bar keeps only the logo, one search icon and one menu
   * button -- eight section links and a name do not fit, and a horizontally
   * scrolling bar hides the thing you were looking for.
   */
  import { browser } from '$app/environment';
  import { palette } from '$lib/console/context';
  import Icon from '$lib/console/ui/Icon.svelte';
  import IconButton from '$lib/console/ui/IconButton.svelte';
  import Kbd from '$lib/console/ui/Kbd.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';
  import NavLinks from './NavLinks.svelte';
  import UserMenu from './UserMenu.svelte';

  const commands = palette();

  /** The command key, where the command key is the one that works. */
  const apple = browser && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

  let sheet: HTMLElement | null = $state(null);
  let sheetOpen = $state(false);
</script>

<header class="topbar">
  <a class="logo" href="/seats">
    <span class="mark" aria-hidden="true"></span>
    <span class="word mono">sieve</span>
  </a>

  <button class="find" type="button" onclick={() => commands.toggle()}>
    <Icon name="search" />
    <span class="say">Find a model, a seat or an action</span>
    <span class="hint">{#if apple}<Kbd>⌘ K</Kbd>{:else}<Kbd>Ctrl K</Kbd>{/if}</span>
  </button>

  <span class="compact" bind:this={sheet}>
    <IconButton label="Find a model, a seat or an action" onclick={() => commands.toggle()}>
      <Icon name="search" />
    </IconButton>
    <IconButton label="Sections" pressed={sheetOpen} onclick={() => (sheetOpen = !sheetOpen)}>
      <Icon name="menu" />
    </IconButton>
  </span>

  <Popover open={sheetOpen} anchor={sheet} onclose={() => (sheetOpen = false)} label="Sections">
    <div class="sheet">
      <NavLinks />
      <UserMenu />
    </div>
  </Popover>

  <nav class="nav" aria-label="Sections">
    <NavLinks />
  </nav>

  <span class="who">
    <UserMenu />
  </span>
</header>

<style>
  .topbar {
    display: flex;
    align-items: center;
    gap: 12px;
    /* the bar sits above the panes: a pane border under it would draw a second line */
    border-bottom: 1px solid var(--c-rule);
    background: var(--c-bg);
    padding-right: 12px;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 9px;
    width: 228px;
    flex: 0 0 228px;
    padding-left: 16px;
    height: 100%;
  }

  .mark {
    width: 14px;
    height: 14px;
    border-radius: 3px;
    background: var(--c-accent);
  }

  .word {
    font-size: 15px;
    font-weight: 500;
    letter-spacing: -0.01em;
  }

  .find {
    display: flex;
    align-items: center;
    gap: 9px;
    flex: 0 1 620px;
    min-width: 0;
    height: 32px;
    padding: 0 9px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-3);
    background: var(--c-panel);
    color: var(--c-muted);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }

  .find:hover {
    border-color: var(--c-rule-hover);
    color: var(--c-ink-2);
  }

  .say {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* the hint goes to the right end, wherever the trigger stopped growing */
  .find :global(.kbd) {
    margin-left: auto;
  }

  .nav {
    margin-left: auto;
  }

  .compact {
    display: none;
    align-items: center;
    gap: 6px;
  }

  .sheet {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 6px;
    min-width: 200px;
  }

  @media (max-width: 900px) {
    .logo {
      width: auto;
      flex: 0 0 auto;
    }

    .find,
    .nav,
    .topbar > :global(.who) {
      display: none;
    }

    .compact {
      display: flex;
      margin-left: auto;
    }
  }
</style>
