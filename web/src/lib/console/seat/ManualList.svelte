<script lang="ts">
  /**
   * Manual mode's table (CONSOLE.md section 6.6): the list as it was hand-made,
   * in its own order, with up/down/drop on every row.
   *
   * There is no ship line here because nothing is competing for the places --
   * the order *is* the answer -- and nothing is ranked twice: a row the pool
   * still holds keeps its score, a row a need keeps out says which need, and an
   * id the pool no longer holds stays on the list marked `missing` rather than
   * disappearing.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { selection } from '../context';
  import { dropManual, nudgeManual } from '../logic/settings';
  import Button from '../ui/Button.svelte';
  import ListStateView from './ListStateView.svelte';
  import ModelRow from './ModelRow.svelte';
  import { emptyText, manualRows, stepIndex, tabStop } from './rows';
  import type { RowAction } from './rows';

  interface Props {
    session: SeatSession;
    /** "Add models" hands the pane back to the all-reachable view */
    onadd: () => void;
  }

  let { session, onadd }: Props = $props();

  const pick = selection();
  const settings = $derived(session.settings);
  const needs = $derived(settings?.needs ?? []);
  const drawn = $derived(manualRows(session.preview, settings?.manual ?? []));
  const empty = $derived(emptyText(session.preview, 'manual'));
  const ids = $derived(drawn.map((one) => one.row.id));

  let list = $state<HTMLElement | null>(null);
  let stop = $state<string | null>(null);
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

  /** The ends of the list have no arrow to draw, so they do not draw one. */
  function actionsAt(at: number): RowAction[] {
    const acts: RowAction[] = [];
    if (at > 0) acts.push('up');
    if (at < drawn.length - 1) acts.push('down');
    acts.push('drop');
    return acts;
  }

  function act(action: RowAction, id: string): void {
    if (!settings) return;
    if (action === 'up') session.edit(nudgeManual(settings, id, -1));
    else if (action === 'down') session.edit(nudgeManual(settings, id, 1));
    else if (action === 'drop') session.edit(dropManual(settings, id));
  }
</script>

<div class="table" role="table" aria-label="The models this seat ships, by hand">
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
      {#each drawn as one, at (one.row.id)}
        <ModelRow
          row={one.row}
          selected={pick.id === one.row.id}
          active={active === one.row.id}
          {needs}
          tone={one.tone}
          actions={actionsAt(at)}
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}
    </div>
  </ListStateView>

  <div class="foot">
    <Button variant="outline" size="md" onclick={onadd}>Add models</Button>
  </div>
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

  .foot {
    padding: 12px 20px 0;
  }

  @media (max-width: 899px) {
    .table {
      --cols: 24px minmax(0, 1fr) minmax(60px, 1fr);
    }
  }
</style>
