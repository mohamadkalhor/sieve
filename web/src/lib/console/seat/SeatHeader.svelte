<script lang="ts">
  /**
   * Who the seat is and what it is for, and the one button that changes what
   * ships (CONSOLE.md section 6.3).
   *
   * The purpose is click-to-edit: it is a sentence, not a form field, and it
   * saves on its own because it is not part of the settings patch.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Button from '$lib/console/ui/Button.svelte';
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

  const profile = $derived(session.profile);
  const mode = $derived(session.settings?.mode ?? profile?.mode ?? 'auto');

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
    <h1 class="name">{session.name}</h1>
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
    <Segmented
      label="List mode"
      value={mode}
      options={[
        { value: 'auto', label: 'Auto' },
        { value: 'manual', label: 'Manual' }
      ]}
      onchange={(value) => session.setMode(value as 'auto' | 'manual')}
    />
    <Button
      variant="primary"
      disabled={session.button.disabled}
      title={session.button.title}
      onclick={() => void session.ship()}
      >{session.shipText}</Button
    >
    <SeatMenu {session} />
  </div>
</header>

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
</style>
