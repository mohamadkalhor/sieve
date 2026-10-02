<script lang="ts">
  /**
   * Runs at: the reasoning effort this seat's agent actually uses (EFFORT.md
   * section 6, mockup callout 1).
   *
   * It sits under the seat's title and above the weights, and it is a setting
   * like any slider: picking one goes through `session.setEffort`, so it is in
   * the same write queue, re-runs the preview, and Ship counts it as a change.
   * `any` is null -- each model scored at the row its router id matched, which
   * is the family's top effort and today's behaviour.
   *
   * Desktop draws one segmented row. The one-column shape has no room for
   * seven segments at 44px, so it draws one 44px row that opens a sheet with
   * every effort in it, `minimal` included. Media seats have no efforts and get
   * no control: a hidden setting that does nothing is one that rots.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { viewport } from '$lib/console/layout/viewport.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';
  import Segmented from '$lib/console/ui/Segmented.svelte';
  import { effortFromOption, effortHint, effortOptions, effortWord } from './rows';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let open = $state(false);
  let anchor = $state<HTMLElement | null>(null);

  const frame = viewport();
  const sheet = $derived(frame.shape === 'single');
  // A sheet whose row a resize has just hidden is a dialog with no anchor.
  $effect(() => {
    if (!sheet) open = false;
  });

  const effort = $derived(session.settings?.effort ?? null);
  const value = $derived(effort ?? 'any');
  const shown = $derived(session.profile?.modality === 'llm' && session.settings !== null);
  const hint = $derived(effortHint(session.shippedEffort, effort, session.effortMoves));

  function choose(option: string): void {
    session.setEffort(effortFromOption(option));
  }
</script>

{#if shown}
  <div class="runs" data-block="runs-at">
    <span class="cap">Runs at</span>
    {#if sheet}
      <button
        type="button"
        class="row"
        bind:this={anchor}
        aria-haspopup="dialog"
        aria-expanded={open}
        onclick={() => (open = !open)}
      >
        <span class="said">Runs at</span>
        <span class="now">{effort ? effortWord(effort) : 'any'}</span>
        <Icon name="chevron" size={14} />
      </button>
    {:else}
      <Segmented
        class="eff"
        label="Effort this seat runs at"
        {value}
        options={effortOptions(false, effort)}
        onchange={choose}
      />
    {/if}
    {#if hint}<span class="hint" data-effort-hint>{hint}</span>{/if}
  </div>

  <Popover {open} {anchor} label="Effort this seat runs at" width={300} onclose={() => (open = false)}>
    <div class="sheet" role="radiogroup" aria-label="Effort this seat runs at">
      {#each effortOptions(true, effort) as option (option.value)}
        <button
          type="button"
          role="radio"
          class="choice"
          aria-checked={value === option.value}
          onclick={() => {
            choose(option.value);
            open = false;
          }}
        >
          {option.label}
          {#if option.value === 'any'}<span class="what">each model at its top effort</span>{/if}
        </button>
      {/each}
    </div>
  </Popover>
{/if}

<style>
  .runs {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    padding: 0 20px 12px;
  }

  .cap {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
  }

  .hint {
    font-size: 12px;
    color: var(--c-muted);
  }

  /* mockup: mono words, the chosen one filled in the accent */
  .runs :global(.eff button) {
    height: 28px;
    padding: 0 10px;
    font-family: var(--f-mono);
    font-size: 12px;
  }

  .runs :global(.eff button[aria-checked='true']) {
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font-weight: 500;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-3);
    background: var(--c-panel);
    color: var(--c-ink);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }

  .said {
    flex: 1 1 auto;
    text-align: left;
    color: var(--c-muted);
  }

  .now {
    font-family: var(--f-mono);
  }

  .sheet {
    display: flex;
    flex-direction: column;
    padding: 4px;
  }

  .choice {
    display: flex;
    gap: 10px;
    min-height: 44px;
    padding: 0 12px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink-2);
    font-family: var(--f-mono);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    align-items: center;
  }

  .choice:hover {
    background: var(--c-raised);
  }

  .choice[aria-checked='true'] {
    background: var(--c-raised);
    color: var(--c-accent);
    box-shadow: inset 2px 0 0 var(--c-accent);
  }

  .what {
    font-family: var(--f-ui);
    font-size: 11px;
    color: var(--c-muted);
  }

  /* the one-column shape: the label is said inside the row, and the row is
     the full width a thumb aims at */
  @media (max-width: 899px) {
    .runs {
      padding: 0 16px 12px;
    }

    .cap {
      display: none;
    }
  }
</style>
