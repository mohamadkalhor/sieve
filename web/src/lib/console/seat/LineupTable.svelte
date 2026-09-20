<script lang="ts">
  /**
   * The table (CONSOLE.md section 6.6): what these settings would ship above the
   * ship line, what is next below it, and then the three ways a model the pool
   * holds is not in the lineup -- a need keeps it out, it was removed by hand,
   * or nothing reachable serves it any more.
   *
   * The rows come from `rows.ts` as data, so the order and the wording are
   * testable without a browser; this file is the markup, the keyboard and the
   * edits. The one piece of state that is not in the session is the tab stop:
   * the table keeps a roving `tabindex` so Tab enters the list once rather than
   * walking every action button, and ↑/↓ then move focus and the inspected
   * model together.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { selection } from '../context';
  import { pin, remove, restore } from '../logic/settings';
  import ListStateView from './ListStateView.svelte';
  import ModelRow from './ModelRow.svelte';
  import ShipLine from './ShipLine.svelte';
  import { emptyText, moveFor, stepIndex, tableSections, tabStop } from './rows';
  import type { DisplayRow, RowAction } from './rows';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const pick = selection();
  const settings = $derived(session.settings);
  const mode = $derived(settings?.mode ?? session.profile?.mode ?? 'auto');
  const needs = $derived(settings?.needs ?? []);
  // In manual mode the pane draws the hand-made list, not this one, so a row it
  // is already showing must not be repeated under the ship line.
  const sections = $derived(
    tableSections(session.preview, session.diff, mode === 'manual' ? (settings?.manual ?? []) : [])
  );
  const empty = $derived(emptyText(session.preview, mode));

  const drawn = $derived<DisplayRow[]>([
    ...sections.lineup,
    ...sections.next,
    ...sections.leaving,
    ...sections.blocked,
    ...sections.removed,
    ...sections.missing
  ]);
  const ids = $derived(drawn.map((row) => row.id));

  let list = $state<HTMLElement | null>(null);
  let stop = $state<string | null>(null);
  /** The tab stop is the focused row, and until one is focused the selection. */
  const active = $derived(tabStop(ids, stop, pick.id));

  function draw(id: string): void {
    stop = id;
    pick.select(id);
    const nodes = list?.querySelectorAll<HTMLElement>('[data-row]');
    nodes?.forEach((node) => {
      if (node.dataset.row === id) node.focus();
    });
  }

  function move(by: number, from: string): void {
    const to = stepIndex(ids.indexOf(from), ids.length, by);
    if (to >= 0 && ids[to] !== from) draw(ids[to]);
  }

  function act(action: RowAction, id: string): void {
    if (!settings) return;
    if (action === 'pin') session.edit(pin(settings, id));
    else if (action === 'remove') session.edit(remove(settings, id));
    else if (action === 'restore') session.edit(restore(settings, id));
  }
</script>

<div class="table" role="table" aria-label="What these settings would ship">
  <div class="head" role="row">
    <span class="caps cell at" role="columnheader">#</span>
    <span class="caps cell model" role="columnheader">Model</span>
    <span class="caps cell score" role="columnheader">Score</span>
    <span class="caps cell task" role="columnheader">Per task</span>
    <span class="caps cell can" role="columnheader">Can do</span>
    <span class="caps cell via" role="columnheader">Via</span>
    <span class="cell acts" role="columnheader"></span>
  </div>

  <ListStateView
    state={session.listing}
    failed={session.failed}
    waitedMs={session.waitedMs}
    {empty}
    onretry={() => void session.refresh()}
  >
    <div class="body" role="rowgroup" bind:this={list}>
      {#each sections.lineup as row (row.id)}
        <ModelRow
          {row}
          move={moveFor(session.diff, row.id)}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          tone="ship"
          actions={['pin', 'remove']}
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}

      {#if sections.lineup.length > 0}
        <ShipLine />
      {/if}

      {#each sections.next as row (row.id)}
        <ModelRow
          {row}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          tone="next"
          dim
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}

      {#each sections.leaving as row (row.id)}
        <ModelRow
          {row}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          tone="next"
          dim
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}

      {#each sections.blocked as row (row.id)}
        <ModelRow
          {row}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          tone="blocked"
          dim
          actions={['pin', 'remove']}
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}

      {#if sections.removed.length > 0}
        <div class="divider" role="row">
          <div class="caps" role="cell" aria-colspan={7}>Removed by you · {sections.removed.length}</div>
        </div>
        {#each sections.removed as row (row.id)}
          <ModelRow
            {row}
            selected={pick.id === row.id}
            active={active === row.id}
            {needs}
            tone="removed"
            dim
            actions={['restore']}
            onselect={pick.select}
            onaction={act}
            onmove={move}
          />
        {/each}
      {/if}

      {#each sections.missing as row (row.id)}
        <ModelRow
          {row}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          tone="missing"
          dim
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}
    </div>
  </ListStateView>
</div>

<style>
  .table {
    --cols: 24px 168px minmax(56px, 1fr) 66px 104px 46px 56px;
  }

  .head {
    display: grid;
    grid-template-columns: var(--cols);
    align-items: center;
    gap: 10px;
    height: 30px;
    padding: 0 12px;
    border-top: 1px solid var(--c-rule);
    border-bottom: 1px solid var(--c-rule);
  }

  .head .cell {
    display: flex;
    align-items: center;
    min-width: 0;
  }

  .head .task,
  .head .score {
    justify-content: flex-end;
  }

  .divider {
    display: flex;
    align-items: center;
    height: 30px;
    padding: 0 12px;
    border-bottom: 1px solid var(--c-rule-soft);
    background: var(--c-panel);
  }

  /* §6.8: at 900px the per-task, can-do and via columns move into the sheet. */
  @media (max-width: 899px) {
    .table {
      --cols: 24px minmax(0, 1fr) minmax(60px, 1fr);
    }
  }
</style>
