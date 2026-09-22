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
  import AllReachable from './AllReachable.svelte';
  import LineupTable from './LineupTable.svelte';
  import ManualList from './ManualList.svelte';
  import NeedChips from './NeedChips.svelte';
  import SeatHeader from './SeatHeader.svelte';
  import ShipStepper from './ShipStepper.svelte';
  import TrimRow from './TrimRow.svelte';
  import ViewSwitch from './ViewSwitch.svelte';
  import WeightsBlock from './WeightsBlock.svelte';

  interface Props {
    session: SeatSession;
    /** which of the table's two views is being shown; the route owns the URL */
    view?: 'lineup' | 'all';
    onview?: (view: 'lineup' | 'all') => void;
  }

  let { session, view = 'lineup', onview = () => {} }: Props = $props();

  const mode = $derived(session.settings?.mode ?? session.profile?.mode ?? 'auto');
  const pool = $derived(session.preview?.pool?.length ?? 0);
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
      <ViewSwitch {view} {pool} {onview} />
      {#if view === 'all'}
        <AllReachable {session} />
      {:else if mode === 'manual'}
        <ManualList {session} onadd={() => onview('all')} />
      {:else}
        <LineupTable {session} />
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
    width: 100%;
    min-width: 0;
    padding: 0 0 24px;
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
