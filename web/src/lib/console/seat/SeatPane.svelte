<script lang="ts">
  /**
   * The seat itself (CONSOLE.md sections 6.2-6.5): the header, the weights, what
   * it must support, how many it ships -- and the table of what those settings
   * would ship, which belongs to the table package and is a list of names until
   * then.
   *
   * The pane owns its own scrolling so a long axis list never moves the column
   * beside it, and it says which of the three ways it has nothing to show --
   * reading, gone, or the list still being ranked -- rather than drawing an
   * empty table.
   */
  import { explainError } from '$lib/api/client';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Button from '$lib/console/ui/Button.svelte';
  import NeedChips from './NeedChips.svelte';
  import SeatHeader from './SeatHeader.svelte';
  import ShipStepper from './ShipStepper.svelte';
  import TrimRow from './TrimRow.svelte';
  import WeightsBlock from './WeightsBlock.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const mode = $derived(session.settings?.mode ?? session.profile?.mode ?? 'auto');
  const rows = $derived(session.preview?.models ?? null);
  const waited = $derived((session.waitedMs / 1000).toFixed(1));
</script>

{#if session.loading && !session.profile && !session.gone}
  <div class="pane">
    <p class="quiet">Reading the seat…</p>
  </div>
{:else if session.gone && !session.profile}
  <div class="pane">
    <h1 class="name">{session.name}</h1>
    <p class="quiet">{explainError(session.gone)}</p>
    <p class="quiet"><a href="/seats">All seats</a></p>
  </div>
{:else}
  <div class="pane">
    <SeatHeader {session} />

    {#if session.settings}
      {#if mode === 'auto'}
        <WeightsBlock {session} />
      {/if}
      <div class="block">
        <NeedChips {session} />
        <div class="line">
          <TrimRow {session} />
          <ShipStepper {session} />
        </div>
      </div>
    {/if}

    <div class="table">
      {#if session.listing === 'error'}
        <p class="quiet">
          {session.failed ?? 'The list could not be read.'}
          <Button variant="ghost" size="sm" onclick={() => void session.refresh()}>Try again</Button>
        </p>
      {:else if session.listing === 'ranking' || session.listing === 'busy'}
        <p class="quiet">{session.listing === 'busy' ? `Still ranking… ${waited}s` : 'Ranking…'}</p>
      {:else if rows === null}
        <p class="quiet">Nothing has been ranked yet.</p>
      {:else if rows.length === 0}
        <p class="quiet">
          These settings ship nothing: no reachable model answers every must-support.
        </p>
      {:else}
        <!-- the table package replaces this list with the rows themselves -->
        <ol class="names" data-slot="lineup">
          {#each rows as row, index (row.id)}
            <li class="row">
              <span class="at">{index + 1}</span>
              <span class="id mono">{row.id}</span>
            </li>
          {/each}
        </ol>
      {/if}
    </div>
  </div>
{/if}

<style>
  .pane {
    height: 100%;
    overflow: auto;
  }

  .block {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 0 20px 14px;
  }

  .line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .table {
    padding: 0 0 24px;
    border-top: 1px solid var(--c-rule);
  }

  .names {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 20px;
    border-bottom: 1px solid var(--c-rule);
    font-size: 13px;
    color: var(--c-ink);
  }

  .at {
    min-width: 14px;
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
  }

  .id {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .name {
    margin: 0 0 8px;
    padding: 16px 20px 0;
    font-family: var(--f-mono);
    font-size: 22px;
    font-weight: 500;
    color: var(--c-ink);
  }

  .quiet {
    margin: 0;
    padding: 12px 20px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--c-muted);
  }
</style>
