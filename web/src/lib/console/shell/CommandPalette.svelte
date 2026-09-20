<script lang="ts">
  /**
   * The command palette's frame (CONSOLE.md section 6.1).
   *
   * A dialog, not a dropdown: it covers the page, takes focus, and gives it
   * back to whatever had it when it closes. 640px wide, dropped 12vh from the
   * top, matching the reference.
   *
   * The commands themselves are not this file's: `palette.results` is filled
   * by package F's `state/palette.svelte.ts` out of C's registry, and this
   * file only decides how a list of them looks and which keys move through it.
   * With nothing registered yet the list is empty and says so.
   */
  import { tick } from 'svelte';
  import { palette } from '$lib/console/context';
  import type { CommandLike } from '$lib/console/contracts';
  import Icon from '$lib/console/ui/Icon.svelte';

  const p = palette();

  let input = $state<HTMLInputElement | null>(null);
  let panel = $state<HTMLElement | null>(null);
  /** where focus was, so closing puts it back */
  let giving: HTMLElement | null = null;

  /** Consecutive runs of one group, in the order the registry produced them. */
  const groups = $derived.by(() => {
    const out: { group: string; items: { cmd: CommandLike; index: number }[] }[] = [];
    p.results.forEach((cmd, index) => {
      const last = out.at(-1);
      if (last && last.group === cmd.group) last.items.push({ cmd, index });
      else out.push({ group: cmd.group, items: [{ cmd, index }] });
    });
    return out;
  });

  $effect(() => {
    if (p.open) {
      giving = document.activeElement as HTMLElement | null;
      void tick().then(() => input?.focus());
      return;
    }
    // Closing: hand focus back, unless the page moved on without us.
    if (giving?.isConnected) giving.focus();
    giving = null;
  });

  function cycle(event: KeyboardEvent): void {
    if (event.key !== 'Tab' || !panel) return;
    const stops = Array.from(
      panel.querySelectorAll<HTMLElement>('input, button:not([disabled]), a[href]')
    );
    if (!stops.length) return;
    const first = stops[0];
    const last = stops[stops.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function onKey(event: KeyboardEvent): void {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      p.move(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      p.move(-1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      p.run();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      p.toggle();
    } else {
      cycle(event);
    }
  }
</script>

{#if p.open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="veil" onclick={() => p.toggle()}>
    <div
      class="palette"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      aria-label="Find a model, a seat or an action"
      bind:this={panel}
      onkeydown={onKey}
      onclick={(event) => event.stopPropagation()}
    >
      <div class="field">
        <Icon name="search" />
        <input
          class="entry"
          type="text"
          role="combobox"
          aria-label="Find a model, a seat or an action"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={p.active >= 0 ? `palette-${p.active}` : undefined}
          placeholder="Find a model, a seat or an action"
          autocomplete="off"
          spellcheck="false"
          bind:this={input}
          bind:value={p.query}
        />
        <kbd>esc</kbd>
      </div>

      <div class="list" id="palette-list" role="listbox" aria-label="Commands">
        {#each groups as group (group.group)}
          <p class="caps group">{group.group}</p>
          {#each group.items as item (item.cmd.id)}
            <div
              class="item"
              id={`palette-${item.index}`}
              role="option"
              aria-selected={item.index === p.active}
              data-active={item.index === p.active}
              tabindex="-1"
              onclick={() => {
                p.active = item.index;
                p.run();
              }}
              onkeydown={() => {}}
            >
              <span class="title">{item.cmd.title}</span>
              {#if item.cmd.hint}<span class="hint mono">{item.cmd.hint}</span>{/if}
            </div>
          {/each}
        {/each}

        {#if !p.results.length}
          <p class="none">
            {p.query ? `Nothing matches “${p.query}”.` : 'Nothing to run yet.'}
          </p>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .veil {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgb(0 0 0 / 45%);
  }

  .palette {
    width: min(640px, calc(100vw - 24px));
    margin: 12vh auto 0;
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-3);
    background: var(--c-panel);
    box-shadow: 0 18px 48px rgb(0 0 0 / 45%);
    overflow: hidden;
  }

  .field {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 0 12px;
    height: 44px;
    border-bottom: 1px solid var(--c-rule);
    color: var(--c-muted);
  }

  .entry {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--c-ink);
    font: inherit;
    font-size: 14px;
    outline: none;
  }

  kbd {
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    padding: 3px 5px;
  }

  .list {
    overflow: auto;
    padding: 6px;
  }

  .group {
    margin: 8px 8px 4px;
  }

  .item {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 7px 9px;
    border-radius: var(--r-2);
    cursor: pointer;
  }

  .item[data-active='true'] {
    background: var(--c-raised);
  }

  .title {
    font-size: 13px;
    color: var(--c-ink);
  }

  .hint {
    margin-left: auto;
    font-size: 11px;
    color: var(--c-muted);
  }

  .none {
    margin: 14px 10px;
    font-size: 13px;
    color: var(--c-muted);
  }
</style>
