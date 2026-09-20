<script lang="ts">
  /**
   * Every reachable model, filtered (CONSOLE.md section 6.6).
   *
   * The lineup says what would ship; this says what could. It is the same rows
   * and the same columns as the table, plus the two things only a pool view
   * needs: a filter over name, id and router id, and the unscored pair -- a
   * model nobody benchmarked is a model the ranking had to price alone, and the
   * ids that matched no catalogue entry at all are not even that yet.
   *
   * A need filters here only in manual mode: in manual mode a model that cannot
   * do the job cannot be added to the hand list, while in auto mode it has to
   * stay visible, because "fails Must support" is a thing you have to be able
   * to see.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import { selection } from '../context';
  import { addManual, pin, remove } from '../logic/settings';
  import Pill from '../ui/Pill.svelte';
  import ListStateView from './ListStateView.svelte';
  import ModelRow from './ModelRow.svelte';
  import UnlinkedRow from './UnlinkedRow.svelte';
  import {
    POOL_LIMIT,
    emptyText,
    poolDisplay,
    standing,
    stepIndex,
    tabStop,
    unlinkedRows,
    unscoredCount
  } from './rows';
  import type { DisplayRow, RowAction } from './rows';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const pick = selection();
  const settings = $derived(session.settings);
  const preview = $derived(session.preview);
  const mode = $derived(settings?.mode ?? session.profile?.mode ?? 'auto');
  const manual = $derived(mode === 'manual');
  const needs = $derived(settings?.needs ?? []);
  const empty = $derived(emptyText(preview, mode));

  let needle = $state('');
  let unscored = $state(false);

  const pool = $derived(preview?.pool ?? []);
  const found = $derived(unscoredCount(preview));
  const drawn = $derived(
    poolDisplay(pool, { needle, unscoredOnly: unscored, needs, mode, limit: POOL_LIMIT })
  );
  const links = $derived(unlinkedRows(preview?.unlinked, { needle, unscoredOnly: unscored }));

  const ids = $derived(drawn.rows.map((row) => row.id));
  let list = $state<HTMLElement | null>(null);
  let stop = $state<string | null>(null);
  const active = $derived(tabStop(ids, stop, pick.id));

  /** What the row buttons do to a model that is already in the catalogue. */
  const actions = $derived<RowAction[]>(manual ? ['add'] : ['pin', 'remove']);

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
    else if (action === 'add') session.edit(addManual(settings, id));
  }

  /** An unlinked id becomes a model first, and joins the list after that. */
  function link(local_id: string): void {
    if (!settings) return;
    void session.linkThen(local_id, manual ? (id) => addManual(settings, id) : (id) => pin(settings, id));
  }

  /** A pool row a need keeps out is drawn like one: it is not shipping. */
  function keepsOut(drawn: DisplayRow): boolean {
    return drawn.kind === 'ranked' && (drawn.row.lacks?.length ?? 0) > 0;
  }

  function where(drawn: DisplayRow): string {
    if (drawn.kind !== 'ranked') return '';
    return standing(drawn.row.id, {
      lineup: preview?.models?.map((one) => one.id) ?? [],
      pinned: settings?.pinned ?? [],
      removed: settings?.removed ?? [],
      needs,
      lacks: drawn.row.lacks ?? []
    });
  }
</script>

<div class="all" role="table" aria-label="Every reachable model">
  <div class="tools">
    <input
      class="find"
      type="search"
      placeholder={`Filter ${pool.length} reachable models…`}
      aria-label={`Filter ${pool.length} reachable models by name, id or router`}
      bind:value={needle}
    />
    <Pill
      label="Unscored only"
      pressed={unscored}
      count={`${found}`}
      title="Models nobody has benchmarked, and ids no catalogue entry matches"
      onclick={() => (unscored = !unscored)}
    />
    {#if unscored && found > 0}
      <a class="link" href="/unscored">score them</a>
    {/if}
  </div>

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
      {#each drawn.rows as row (row.id)}
        <ModelRow
          {row}
          selected={pick.id === row.id}
          active={active === row.id}
          {needs}
          standing={where(row)}
          tone={keepsOut(row) ? 'blocked' : 'ship'}
          dim={keepsOut(row)}
          {actions}
          onselect={pick.select}
          onaction={act}
          onmove={move}
        />
      {/each}

      {#if drawn.more > 0}
        <p class="more caps">{drawn.more} more — keep typing</p>
      {/if}

      {#each links as row (row.local_id)}
        <UnlinkedRow
          local_id={row.local_id}
          name={row.name}
          action={manual ? 'add' : 'pin'}
          busy={session.linking === row.local_id}
          onlink={() => link(row.local_id)}
        />
      {/each}
    </div>
  </ListStateView>
</div>

<style>
  .tools {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 20px;
    border-bottom: 1px solid var(--c-rule);
  }

  .find {
    flex: 0 1 320px;
    height: 28px;
    padding: 0 10px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-pill);
    background: transparent;
    color: var(--c-ink);
    font-family: var(--f-ui);
    font-size: 12px;
  }

  .find::placeholder {
    color: var(--c-muted);
  }

  .link {
    color: var(--c-accent);
    font-family: var(--f-ui);
    font-size: 12px;
  }

  .head {
    display: grid;
    grid-template-columns: 24px 168px minmax(56px, 1fr) 66px 104px 46px 56px;
    align-items: center;
    gap: 10px;
    height: 30px;
    padding: 0 12px;
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

  .more {
    display: flex;
    align-items: center;
    height: 40px;
    padding: 0 12px;
    border-bottom: 1px solid var(--c-rule-soft);
  }

  @media (max-width: 899px) {
    .head {
      grid-template-columns: 24px minmax(0, 1fr) minmax(60px, 1fr);
    }
  }
</style>
