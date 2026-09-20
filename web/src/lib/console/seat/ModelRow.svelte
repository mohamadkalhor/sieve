<script lang="ts">
  /**
   * One row of the table (CONSOLE.md section 6.6, REVIEW.md findings 10 and 13).
   *
   * The props are the section's, with one deliberate change: `row` is the
   * display-row union rather than `Listed`, because a `missing` id and a
   * `leaving` id are not a model the server ranked and must not be dressed as
   * one. A bare row shows a dashed score, cost and capabilities with a reason
   * on the hover, and its actions are only the ones that make sense for
   * something nobody has read.
   *
   * The row is the keyboard's unit, so it holds its own key handling -- one
   * copy instead of one per list -- and the guards that make it safe: a key or
   * a click that started inside a button, a link or a field is that control's,
   * not the row's, and a composing keystroke (a Japanese IME mid-word) is
   * nobody's here (finding 13).
   */
  import type { Need } from '$lib/api/client';
  import { viewport } from '$lib/console/layout/viewport.svelte';
  import { NEED_TAG } from '../logic/abilities';
  import type { RowMove } from '../logic/diff';
  import { perTask } from '../logic/money';
  import Bar from '../ui/Bar.svelte';
  import Icon from '../ui/Icon.svelte';
  import IconButton from '../ui/IconButton.svelte';
  import { ACTION_ICON, actionLabel, canDo, via } from './rows';
  import type { DisplayRow, RowAction } from './rows';

  interface Props {
    row: DisplayRow;
    move?: RowMove | null;
    selected?: boolean;
    dim?: boolean;
    tone?: 'ship' | 'next' | 'blocked' | 'removed' | 'missing';
    needs?: readonly Need[];
    actions?: RowAction[];
    /** the one row holding the list's tab stop (roving tabindex) */
    active?: boolean;
    /** where this model stands in the seat, for a view that shows the pool */
    standing?: string;
    onselect?: (id: string) => void;
    onaction?: (action: RowAction, id: string) => void;
    onmove?: (by: number, id: string) => void;
  }

  let {
    row,
    move = null,
    selected = false,
    dim = false,
    tone = 'ship',
    needs = [],
    actions = [],
    active = false,
    standing = '',
    onselect = () => {},
    onaction = () => {},
    onmove = () => {}
  }: Props = $props();

  const listed = $derived(row.kind === 'ranked' ? row.row : null);
  const pinned = $derived(listed?.pinned === true);
  const noScore = $derived(listed?.scored === false);
  const measured = $derived(listed?.cost_from === 'telemetry');

  /**
   * §6.8: on the one-column screen a phone is holding, the row's buttons are
   * what a thumb aims at, so they grow to the 44px the section asks for.
   */
  const frame = viewport();
  const hit = $derived<28 | 30 | 44>(frame.shape === 'single' ? 44 : 30);

  /** What a need keeps this model out of, when the row is a blocked one. */
  const fails = $derived(
    tone === 'blocked' && listed?.lacks?.length
      ? `fails ${listed.lacks.map((need) => NEED_TAG[need]).join(' · ')}`
      : ''
  );

  // Finding 10: a row nobody read says so, and never a zero.
  const scoreText = $derived(listed ? listed.score.toFixed(2) : '—');
  const scoreTitle = $derived(
    listed
      ? `${Math.round(listed.score * 100)}%`
      : 'not in the ranked pool: nothing was measured for it'
  );
  const barTitle = $derived(listed ? '' : scoreTitle);
  const money = $derived(
    listed
      ? perTask(listed.cost_per_task)
      : { text: '—', title: 'not in the ranked pool: no cost was reported for it' }
  );
  const said = $derived(
    listed ? canDo(listed, needs) : { text: '', warn: false, title: 'not in the ranked pool: nothing is known about it' }
  );
  const routers = $derived(listed ? via(listed.local_ids) : '');
  const routerTitle = $derived(
    listed ? listed.local_ids.join('\n') : 'not in the ranked pool: no router reports it'
  );

  /** `Delete` in a list that has no `remove` is the list's own way out. */
  const deleteAction = $derived(
    actions.includes('remove') ? 'remove' : actions.includes('drop') ? 'drop' : null
  );

  function fromNested(target: EventTarget | null): boolean {
    const el = target as HTMLElement | null;
    return Boolean(el?.closest?.('button, a, input, select, textarea, [contenteditable="true"]'));
  }

  function onclick(event: MouseEvent): void {
    if (fromNested(event.target)) return;
    onselect(row.id);
  }

  function onkeydown(event: KeyboardEvent): void {
    if (event.isComposing || fromNested(event.target)) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      onmove(event.key === 'ArrowDown' ? 1 : -1, row.id);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      onselect(row.id);
      return;
    }
    if ((event.key === 'p' || event.key === 'P') && actions.includes('pin')) {
      event.preventDefault();
      onaction('pin', row.id);
      return;
    }
    if (event.key === 'Delete' && deleteAction) {
      event.preventDefault();
      onaction(deleteAction, row.id);
    }
  }
</script>

<div
  class="row"
  role="row"
  data-row={row.id}
  data-tone={tone}
  data-selected={selected ? 'true' : undefined}
  data-dim={dim ? 'true' : undefined}
  aria-selected={selected}
  tabindex={active ? 0 : -1}
  title={row.id}
  {onclick}
  {onkeydown}
>
  <div class="cell rank num" role="cell">{row.rank}</div>

  <div class="cell model" role="cell">
    <span class="name">{row.name}</span>
    {#if move}<span class="move">{move.text}</span>{/if}
    {#if standing}<span class="standing">{standing}</span>{/if}
    {#if row.note}<span class="note">{row.note}</span>{/if}
    {#if noScore}
      <span class="tag" title="No source has benchmarked it: it ranks on price alone">no score</span>
    {/if}
    {#if fails}<span class="tag warn">{fails}</span>{/if}
    {#if tone === 'missing'}
      <span class="tag warn" title="Nothing reachable serves it, so it cannot ship">not reachable now</span>
    {/if}
  </div>

  <div class="cell score" role="cell">
    <div class="trackwrap">
      <Bar value={listed ? listed.score : null} tone={dim ? 'dim' : 'accent'} title={barTitle} />
    </div>
    <span class="num figure" title={scoreTitle}>{scoreText}</span>
  </div>

  <div class="cell task" role="cell">
    {#if measured}<span class="dot" title="from this seat's own traffic"></span>{/if}
    <span class="num" title={money.title}>{money.text}</span>
  </div>

  <div class="cell can" role="cell">
    <span class="word" data-warn={said.warn ? 'true' : undefined} title={said.title}>{said.text || '—'}</span>
  </div>

  <div class="cell via" role="cell">
    <span class="word" title={routerTitle}>{routers || '—'}</span>
  </div>

  <div class="cell acts" role="cell">
    {#each actions as action (action)}
      <IconButton
        label={actionLabel(action, row.name, action === 'pin' && pinned)}
        title={actionLabel(action, row.name, action === 'pin' && pinned)}
        pressed={action === 'pin' ? pinned : undefined}
        size={hit}
        onclick={() => onaction(action, row.id)}
      >
        <Icon name={ACTION_ICON[action]} />
      </IconButton>
    {/each}
  </div>
</div>

<style>
  .row {
    display: grid;
    grid-template-columns: var(--cols, 24px 168px minmax(56px, 1fr) 66px 104px 46px 56px);
    align-items: center;
    gap: 10px;
    min-height: 40px;
    padding: 0 12px;
    border-bottom: 1px solid var(--c-rule-soft);
    color: var(--c-ink);
    cursor: default;
  }

  .row[data-tone='ship'] {
    min-height: 44px;
  }

  /* §6.8: below 900px the row is the rank, the model and the score -- per-task,
     can-do and via are things the sheet beside the model already says -- and
     the move and the tags sit under the name instead of beside it, because
     there is no longer a column's worth of room to put them in. */
  @media (max-width: 899px) {
    .row {
      align-items: start;
      padding: 6px 12px;
    }

    .cell.task,
    .cell.can,
    .cell.via {
      display: none;
    }

    .cell.model {
      flex-direction: column;
      align-items: flex-start;
      gap: 2px;
    }

    .cell.rank,
    .cell.acts {
      align-items: center;
      align-self: center;
    }
  }

  @media (pointer: coarse) {
    .row {
      min-height: 48px;
    }
  }

  .row[data-dim='true'] {
    opacity: 0.72;
  }

  .row[data-tone='missing'] .name,
  .row[data-tone='missing'] .num {
    color: var(--c-ink-2);
  }

  .row:hover {
    background: var(--c-raised);
  }

  .row[data-selected='true'] {
    background: var(--c-row-sel);
    box-shadow: inset 2px 0 0 var(--c-accent);
  }

  .row:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: -2px;
  }

  .cell {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .num {
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
  }

  .rank {
    justify-content: flex-end;
  }

  .model {
    flex-wrap: wrap;
    row-gap: 2px;
  }

  .name {
    overflow: hidden;
    font-size: 13px;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .move {
    font-size: 11px;
    font-weight: 600;
    color: var(--c-accent);
  }

  .note {
    font-size: 11px;
    font-weight: 600;
    color: var(--c-warn);
  }

  .standing {
    font-size: 11px;
    font-weight: 600;
    color: var(--c-ink-2);
    white-space: nowrap;
  }

  .tag {
    padding: 0 5px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    font-size: 10px;
    line-height: 15px;
    color: var(--c-muted);
    white-space: nowrap;
  }

  .tag.warn {
    border-color: var(--c-warn);
    color: var(--c-warn);
  }

  .trackwrap {
    flex: 1 1 auto;
    min-width: 40px;
  }

  .figure {
    font-size: 12px;
    color: var(--c-ink-2);
  }

  .task {
    justify-content: flex-end;
  }

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--c-accent);
  }

  .word {
    overflow: hidden;
    font-size: 11px;
    color: var(--c-ink-2);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .word[data-warn='true'] {
    color: var(--c-warn);
  }

  .acts {
    justify-content: flex-end;
    gap: 2px;
  }
</style>
