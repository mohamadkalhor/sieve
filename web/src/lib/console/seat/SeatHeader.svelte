<script lang="ts">
  /**
   * Who the seat is and what it is for, and the one button that changes what
   * ships (CONSOLE.md section 6.3).
   *
   * The purpose is click-to-edit: it is a sentence, not a form field, and it
   * saves on its own because it is not part of the settings patch.
   *
   * Section 6.8: from 1024px down the seats pane is not drawn at all, so this
   * header is where a seat is changed -- the name becomes a button that opens
   * the same list in a popover. Both readings of the name are in the markup,
   * because which one is drawn is a media query here rather than a measurement;
   * a `display: none` button cannot be tabbed to, so only one of them is ever
   * reachable.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { viewport } from '$lib/console/layout/viewport.svelte';
  import SeatsPane from '$lib/console/seats/SeatsPane.svelte';
  import Button from '$lib/console/ui/Button.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';
  import Segmented from '$lib/console/ui/Segmented.svelte';
  import Toast from '$lib/console/ui/Toast.svelte';
  import AskBar from './AskBar.svelte';
  import SeatMenu from './SeatMenu.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let editing = $state(false);
  let typed = $state('');
  let swapping = $state(false);
  let anchor = $state<HTMLElement | null>(null);

  const profile = $derived(session.profile);
  const mode = $derived(session.settings?.mode ?? profile?.mode ?? 'auto');

  const frame = viewport();
  // A popover whose button has just been hidden by a resize is a dialog with no
  // anchor: close it rather than leave it floating over nothing.
  $effect(() => {
    if (frame.shape === 'three' || frame.shape === 'drawer') swapping = false;
  });

  function begin(): void {
    typed = profile?.purpose ?? '';
    editing = true;
  }

  async function save(): Promise<void> {
    if (!editing) return;
    editing = false;
    await session.setPurpose(typed);
  }
</script>

<header class="head">
  <div class="who">
    <h1 class="name">
      <span class="plain">{session.name}</span>
      <button
        class="switcher"
        type="button"
        bind:this={anchor}
        aria-haspopup="dialog"
        aria-expanded={swapping}
        onclick={() => (swapping = !swapping)}
      >
        {session.name}
        <Icon name="chevron" size={14} />
      </button>
    </h1>
    {#if editing}
      <input
        class="purpose-edit"
        aria-label="What this seat is for"
        spellcheck="false"
        value={typed}
        oninput={(event) => (typed = event.currentTarget.value)}
        onblur={save}
        onkeydown={(event) => {
          if (event.key === 'Enter') void save();
          if (event.key === 'Escape') editing = false;
        }}
      />
    {:else}
      <button type="button" class="purpose" onclick={begin}>
        {profile?.purpose || 'What is this seat for?'}
      </button>
    {/if}
  </div>

  <div class="acts">
    <div class="modes">
      <Segmented
        label="List mode"
        value={mode}
        options={[
          { value: 'auto', label: 'Auto' },
          { value: 'manual', label: 'Manual' }
        ]}
        onchange={(value) => session.setMode(value as 'auto' | 'manual')}
      />
    </div>
    <div class="ship">
      <Button
        variant="primary"
        disabled={session.button.disabled}
        title={session.button.title}
        onclick={() => void session.ship()}
        >{session.shipText}</Button
      >
    </div>
    <div class="menu"><SeatMenu {session} /></div>
  </div>
</header>

<Popover open={swapping} {anchor} label="Seats" width={300} onclose={() => (swapping = false)}>
  <SeatsPane />
</Popover>

<AskBar {session} />

{#if session.said}
  <Toast message={session.said} />
{/if}

<style>
  .head {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 16px 20px 12px;
  }

  .who {
    min-width: 0;
    flex: 1 1 auto;
  }

  .name {
    margin: 0;
    font-family: var(--f-mono);
    font-size: 22px;
    font-weight: 500;
    color: var(--c-ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* section 6.8: while the seats pane is drawn the name is only a title; below
     1024 it is the way to change seats. Both are in the markup and exactly one
     is drawn -- a name that is a button only where a button is wanted cannot
     offer a click that does nothing. */
  .switcher {
    display: none;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    transition: color 120ms ease;
  }

  .switcher:hover {
    color: var(--c-accent);
  }

  @media (max-width: 1023px) {
    .plain {
      display: none;
    }

    .switcher {
      display: inline-flex;
    }
  }

  @media (pointer: coarse) {
    /* the seat's name is the only way to another seat down here */
    .switcher {
      min-height: 44px;
    }
  }

  .purpose,
  .purpose-edit {
    display: block;
    width: 100%;
    margin-top: 2px;
    padding: 2px 0;
    border: 0;
    background: transparent;
    color: var(--c-muted);
    font-family: inherit;
    font-size: 12px;
    line-height: 1.4;
    text-align: left;
  }

  .purpose {
    cursor: text;
  }

  .purpose:hover {
    color: var(--c-ink-2);
  }

  .purpose-edit {
    border-bottom: 1px solid var(--c-rule);
    color: var(--c-ink-2);
  }

  .purpose-edit:focus {
    outline: none;
    border-bottom-color: var(--c-accent);
  }

  .acts {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
  }

  /* The three groups are wrappers only: at every width above the phone rule
     they are `display: contents`, so the header lays out exactly as it did
     before they existed. They exist to be placed as grid areas below 900px. */
  .modes,
  .ship,
  .menu {
    display: contents;
  }

  /* §6.8, one column: name and the menu on the first row, the purpose full
     width under it, and Auto/Manual beside a Ship that takes what is left.
     Sharing one row was what cut the name to "code:" and broke the purpose
     into one word per line. */
  @media (max-width: 899px) {
    .head {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      grid-template-areas:
        'name name menu'
        'purpose purpose purpose'
        'modes ship ship';
      align-items: center;
      column-gap: 8px;
      row-gap: 8px;
      padding: 12px 16px;
    }

    .who,
    .acts {
      display: contents;
    }

    .name {
      grid-area: name;
      min-width: 0;
    }

    .purpose,
    .purpose-edit {
      grid-area: purpose;
      min-height: 44px;
      margin-top: 0;
    }

    .modes {
      grid-area: modes;
      display: block;
    }

    .ship {
      grid-area: ship;
      display: block;
    }

    .ship :global(.btn) {
      width: 100%;
    }

    .menu {
      grid-area: menu;
      display: block;
    }
  }
</style>
