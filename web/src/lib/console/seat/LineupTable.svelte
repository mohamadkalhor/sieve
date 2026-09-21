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
  import { reducedMotion } from '$lib/motion/reduced';
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

  /**
   * Section 6.9: when an answer reorders the list, a row slides to its new place
   * instead of appearing there -- 160ms, long enough to follow the row you were
   * reading and short enough not to be in the way of the next keystroke.
   * Nothing moves when motion is not wanted.
   *
   * Svelte's `animate:flip` is the documented spelling of this, but it attaches
   * to an element and these each blocks render a component (`component_invalid_
   * directive`). The arithmetic is the same one FLIP names -- read every row's
   * top before the DOM changes, read them again after, and translate the
   * difference away -- done here, on the rows themselves.
   */
  const glideMs = $derived($reducedMotion ? 0 : 160);

  function tops(root: HTMLElement | null): Map<string, number> {
    const out = new Map<string, number>();
    if (!root) return out;
    for (const row of root.querySelectorAll<HTMLElement>('[data-row]')) {
      const id = row.dataset.row;
      if (id) out.set(id, row.getBoundingClientRect().top);
    }
    return out;
  }

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

  // Where every row sat before the list changed, read before the DOM is patched
  // and again after it, and the two readings are the whole of FLIP.
  let seated = new Map<string, number>();

  $effect.pre(() => {
    void drawn.length;
    seated = tops(list);
  });

  $effect(() => {
    void drawn.length;
    if (!list) return;
    const moved = tops(list);
    const ms = glideMs;
    if (ms > 0 && seated.size > 0) {
      for (const [id, top] of moved) {
        const was = seated.get(id);
        if (was === undefined || Math.abs(was - top) < 1) continue;
        list
          .querySelector<HTMLElement>(`[data-row="${CSS.escape(id)}"]`)
          ?.animate(
            [{ transform: `translateY(${Math.round(was - top)}px)` }, { transform: 'none' }],
            { duration: ms, easing: 'ease' }
          );
      }
    }
    seated = moved;
  });

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
          onselect={(id) => pick.select(id)}
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
          onselect={(id) => pick.select(id)}
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
          onselect={(id) => pick.select(id)}
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
          onselect={(id) => pick.select(id)}
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
            onselect={(id) => pick.select(id)}
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
          onselect={(id) => pick.select(id)}
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

  /* §6.8: at 900px the per-task, can-do and via columns move into the sheet,
     and so do the row's buttons -- a thumb aims at them there, and in the row
     they landed on top of the score. What is left is the rank, the model with
     its tags under the name, and the score. */
  @media (max-width: 899px) {
    .table {
      --cols: 22px minmax(0, 1fr) 88px;
    }

    .head .task,
    .head .can,
    .head .via,
    .head .acts {
      display: none;
    }
  }
</style>
