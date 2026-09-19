<script lang="ts">
  /**
   * The one question the header asks (CONSOLE.md section 6.3).
   *
   * Delete is not asked twice: the sentence says what stops, and if the server
   * says the seat is still shipping something the seat's own `destroy` reports
   * it. Copy and rename take the name typed here and then go to it, because a
   * seat that has just been made somewhere else is the seat you want to be
   * standing on.
   */
  import { goto } from '$app/navigation';
  import Button from '$lib/console/ui/Button.svelte';
  import type { SeatSession } from '$lib/console/state/seat.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let busy = $state(false);

  const what = $derived(session.ask.what);

  async function confirm(): Promise<void> {
    if (busy) return;
    busy = true;
    try {
      if (what === 'copy') {
        const made = await session.copy(session.ask.name);
        if (made) await goto(`/seats/${encodeURIComponent(made)}`);
      } else if (what === 'rename') {
        const named = await session.rename(session.ask.name);
        if (named) await goto(`/seats/${encodeURIComponent(named)}`);
      } else if (what === 'delete') {
        const gone = await session.destroy();
        if (gone) await goto('/seats');
      }
    } finally {
      busy = false;
    }
  }
</script>

{#if what}
  <div class="ask">
    {#if what === 'delete'}
      <span class="words">Delete {session.name}? The combo it ships stops being updated.</span>
      <Button variant="danger" size="sm" disabled={busy} onclick={confirm}>Delete</Button>
    {:else}
      <label class="field" for="ask-name">{what === 'copy' ? 'Copy to' : 'Rename to'}</label>
      <input
        id="ask-name"
        class="name"
        spellcheck="false"
        autocomplete="off"
        value={session.ask.name}
        oninput={(event) => (session.ask.name = event.currentTarget.value)}
        onkeydown={(event) => {
          if (event.key === 'Enter') void confirm();
          if (event.key === 'Escape') session.askAbout('');
        }}
      />
      <Button variant="primary" size="sm" disabled={busy} onclick={confirm}>
        {what === 'copy' ? 'Copy' : 'Rename'}
      </Button>
    {/if}
    <Button variant="ghost" size="sm" onclick={() => session.askAbout('')}>Cancel</Button>
  </div>
{/if}

<style>
  .ask {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 20px 10px;
    flex-wrap: wrap;
  }

  .words {
    font-size: 12px;
    color: var(--c-ink-2);
  }

  .field {
    font-size: 11px;
    color: var(--c-muted);
  }

  .name {
    min-width: 0;
    flex: 1 1 160px;
    padding: 5px 8px;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: var(--c-pane);
    color: var(--c-ink);
    font-family: var(--f-mono);
    font-size: 12px;
  }

  .name:focus {
    outline: none;
    border-color: var(--c-accent);
  }
</style>
