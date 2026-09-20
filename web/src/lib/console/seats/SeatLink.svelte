<script lang="ts">
  /**
   * One seat in the list (CONSOLE.md section 6.2).
   *
   * A link, not a button: the seat's address is real, so the middle click, the
   * copied URL and the browser's own history all work, and SvelteKit navigates
   * it without a reload.
   *
   * The line under the name is the first model the gateway holds -- and the two
   * ways of having none are drawn differently on purpose: no chain was ever
   * read (`live` is null) says nothing, while a seat that ships nothing says so.
   */
  import type { SeatRow } from '$lib/api/client';
  import { badge } from '../logic/seats';
  import { secondLine } from './view';

  interface Props {
    row: SeatRow;
    selected: boolean;
  }

  let { row, selected }: Props = $props();

  const line = $derived(secondLine(row));
  const mark = $derived(badge(row));
  const changing = $derived(typeof row.changes === 'number' && row.changes > 0);
</script>

<a
  class="seat"
  class:on={selected}
  href={`/seats/${encodeURIComponent(row.name)}`}
  aria-current={selected ? 'page' : undefined}
  title={row.purpose}
>
  <span class="dot" class:changing aria-hidden="true"></span>
  <span class="text">
    <span class="name">{row.name}</span>
    {#if line}<span class="line">{line}</span>{/if}
  </span>
  {#if mark}<span class="badge" class:accent={mark.tone === 'accent'}>{mark.text}</span>{/if}
</a>

<style>
  .seat {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 46px;
    padding: 0 8px;
    border-radius: var(--r-2);
    color: inherit;
    text-decoration: none;
  }
  .seat:hover {
    background: var(--c-panel);
  }
  .seat.on {
    background: var(--c-raised);
  }
  .seat:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: -2px;
  }
  .dot {
    flex: none;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--c-rule-hover);
  }
  .dot.changing {
    background: var(--c-accent);
  }
  .text {
    min-width: 0;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .name {
    font-family: var(--f-mono);
    font-size: 13px;
    line-height: 16px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .line {
    font-size: 11px;
    line-height: 14px;
    color: var(--c-muted);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .badge {
    flex: none;
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
  }
  .badge.accent {
    color: var(--c-accent);
  }
</style>
