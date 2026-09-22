<script lang="ts">
  /**
   * How many models this seat ships (CONSOLE.md section 6.5).
   *
   * In manual mode the count still matters -- it is what the row of pins is
   * made of -- but there is no limit to raise or lower, so the stepper is
   * replaced by the sentence that says so.
   *
   * The buttons are local rather than `IconButton`s: the floor has to be
   * visible, and the set-minimum case is exactly the one `disabled` carries.
   */
  import { SHIP_MIN } from '$lib/profile/tune';
  import { setShip } from '$lib/console/logic/settings';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const settings = $derived(session.settings);
  const mode = $derived(settings?.mode ?? session.profile?.mode ?? 'auto');
  const count = $derived(session.preview?.models.length ?? session.lineup?.length ?? 0);
</script>

<div class="ships">
  {#if mode === 'manual'}
    <span class="text">{count} in the list · no limit</span>
  {:else if settings}
    <span class="cap">ships</span>
    <button
      type="button"
      class="step"
      aria-label="One fewer"
      disabled={settings.ship <= SHIP_MIN}
      onclick={() => session.edit(setShip(settings, settings.ship - 1))}
    >
      <Icon name="minus" size={14} />
    </button>
    <span class="n">{settings.ship}</span>
    <button
      type="button"
      class="step"
      aria-label="One more"
      onclick={() => session.edit(setShip(settings, settings.ship + 1))}
    >
      <Icon name="plus" size={14} />
    </button>
  {/if}
</div>

<style>
  .ships {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-left: auto;
  }

  .cap {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
    margin-right: 2px;
  }

  .text {
    font-size: 12px;
    color: var(--c-muted);
  }

  .step {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink-2);
    cursor: pointer;
  }

  .step:hover:not(:disabled) {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  .step:disabled {
    opacity: 0.4;
    cursor: default;
  }

  @media (max-width: 899px) {
    .step {
      width: 44px;
      height: 44px;
    }
  }

  .n {
    min-width: 20px;
    text-align: center;
    font-family: var(--f-mono);
    font-size: 13px;
    color: var(--c-ink);
  }
</style>
