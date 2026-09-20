<script lang="ts">
  /**
   * What you can do about this model, pinned to the bottom of the inspector
   * (CONSOLE.md section 6.7).
   *
   * Every button goes through `session.edit(...)`, so an edit from the panel
   * travels the same road as an edit from a row: the debounce, the preview, and
   * the history entry. The label says what the button does; the title says what
   * it does *to this model*, which is what a screen reader hears.
   *
   * Auto seats offer the two decisions a person makes about a model -- bring it
   * up, or keep it out -- and the manual list offers the one decision that
   * means anything there. "Pin first" and "Never ship" are the same words the
   * table's rows use, because they are the same actions.
   */
  import Button from '../ui/Button.svelte';
  import { addManual, dropManual, pin, remove, restore } from '../logic/settings';
  import { actionLabel } from '../seat/rows';
  import type { SeatSession } from '../state/seat.svelte';

  interface Props {
    session: SeatSession;
    id: string;
    /** the name to say in a title, which may be an id when nothing named it */
    name: string;
  }

  let { session, id, name }: Props = $props();

  const settings = $derived(session.settings);
  /** A hand-made list is the only thing that changes a manual seat. */
  const manual = $derived(settings !== null && settings.mode === 'manual');
  const up = $derived(settings?.pinned.includes(id) ?? false);
  const out = $derived(settings?.removed.includes(id) ?? false);
  const listed = $derived(settings?.manual.includes(id) ?? false);

  function act(next: ReturnType<typeof pin>): void {
    session.edit(next);
  }
</script>

{#if settings !== null}
  <footer class="acts" data-block="actions">
    {#if manual}
      <Button
        variant="outline"
        class="grow"
        title={actionLabel(listed ? 'drop' : 'add', name)}
        onclick={() => act(listed ? dropManual(settings, id) : addManual(settings, id))}
      >
        {listed ? 'Take off the list' : 'Add to list'}
      </Button>
    {:else}
      <Button
        variant="outline"
        class="grow"
        title={actionLabel('pin', name, up)}
        onclick={() => act(pin(settings, id))}
      >
        {up ? 'Unpin' : 'Pin first'}
      </Button>
      <Button
        variant="outline"
        class="grow"
        title={actionLabel(out ? 'restore' : 'remove', name)}
        onclick={() => act(out ? restore(settings, id) : remove(settings, id))}
      >
        {out ? 'Put back' : 'Never ship'}
      </Button>
    {/if}
  </footer>
{/if}

<style>
  .acts {
    display: flex;
    gap: 8px;
    /* pinned to the bottom of the pane, and the last thing to scroll past */
    margin-top: auto;
    padding-top: 4px;
  }
  /* the class travels to the button, so it is matched from here (TopBar does
     the same) -- both buttons share the width and the row's 40px height */
  .acts > :global(.grow) {
    --btn-h: 40px;
    flex: 1;
  }
</style>
