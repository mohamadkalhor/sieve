<script lang="ts">
  /**
   * The media ranking: one horizontal bar per model, best first.
   *
   * The Field scatter plots quality against cost, so a point needs both. Media
   * models almost never have both — 5 of 313 scored media models carry a price
   * against 60 of 60 LLMs — so a scatter here is a handful of dots over an
   * empty field, and it reads as "there are five text-to-image models". That is
   * false, and it is the same failure the rest of this system exists to avoid:
   * an absence rendering as a fact.
   *
   * So where cost cannot be answered, this answers what can be: which of these
   * is best, in order, with the number.
   */
  import { sourceWords, type Leaderboard } from '$lib/types';

  interface Props {
    board: Leaderboard;
    onmetric?: (metric: string) => void;
  }
  let { board, onmetric }: Props = $props();

  const DEFAULT_ROWS = 12;
  let showAll = $state(false);

  const rows = $derived(board.rows ?? []);
  const shown = $derived(showAll ? rows : rows.slice(0, DEFAULT_ROWS));

  /**
   * Bar length against the population's own minimum and maximum, both printed
   * under the chart.
   *
   * Text-to-image runs 465 to 1178, so the top twelve all land above 85% and
   * the bars look flat. That flatness is **true** and it is the point: the
   * leaders are within a few per cent of each other. Rescaling to fill the
   * width would manufacture a difference the data does not contain.
   */
  function width(value: number): number {
    const low = board.low ?? 0;
    const high = board.high ?? 1;
    if (high <= low) return 100;
    return Math.max(1, ((value - low) / (high - low)) * 100);
  }

  const round = (v: number) => (Math.abs(v) >= 100 ? Math.round(v) : Number(v.toFixed(3)));
  const label = (m: string) => m.replace('elo:', '').replace(/_/g, ' ');

  /**
   * Who ranked these, in words.
   *
   * "Ranked by elo" names a unit and no author, which reads as though some
   * arena outside this system produced it. Every media score here is
   * Artificial Analysis's own, and the one case where that would stop being
   * true — a second scoreboard folded in — is exactly the case the reader
   * needs to see, so this is derived from what the rows came from rather than
   * written down as a constant.
   */
  const who = $derived(sourceWords(board.sources));
  const unit = $derived(board.metric.startsWith('elo') ? 'Elo' : label(board.metric));
</script>

{#if board.reason}
  <div class="note" role="status">
    <h3>No ranking for {board.modality}</h3>
    <p>{board.reason}</p>
  </div>
{:else if rows.length === 0}
  <div class="note" role="status">
    <h3>Nothing scored yet for {board.modality}</h3>
    <p>Pull a source that measures it, and this fills in.</p>
  </div>
{:else}
  <section class="board">
    <header>
      <div>
        <h3>
          {#if who}{who} {unit}{:else}Ranked by {label(board.metric)}{/if}
          {#if board.metric.includes(':')}<span class="cut">· {label(board.metric)}</span>{/if}
        </h3>
        <p class="sub">
          {rows.length} model{rows.length === 1 ? '' : 's'}, best first.
          {#if !board.scatter_ok}
            Only {board.priced} of {board.scored} have a price, so cost cannot be plotted
            against quality here.
          {/if}
        </p>
      </div>

      {#if (board.metrics ?? []).length > 1}
        <label class="metric">
          <span>Metric</span>
          <select value={board.metric} onchange={(e) => onmetric?.(e.currentTarget.value)}>
            {#each board.metrics ?? [] as m (m)}
              <option value={m}>{label(m)}</option>
            {/each}
          </select>
        </label>
      {/if}
    </header>

    <ol class="bars">
      {#each shown as row, i (row.model_id)}
        <li>
          <span class="rank">{i + 1}</span>
          <span class="who">
            <span class="name">{row.name}</span>
            <span class="creator">
              {row.creator}{#if row.merged?.length}
                <span class="merged" title={row.merged.join('\n')}
                  >· {row.merged.length} duplicate{row.merged.length === 1 ? '' : 's'} merged</span
                >
              {/if}
            </span>
          </span>
          <span class="track"><span class="bar" style:width={`${width(row.value)}%`}></span></span>
          <span class="value">{round(row.value)}</span>
        </li>
      {/each}
    </ol>

    <footer>
      <span class="scale">
        bar spans {round(board.low ?? 0)} to {round(board.high ?? 0)}, this field's own range
      </span>
      {#if rows.length > DEFAULT_ROWS}
        <button type="button" onclick={() => (showAll = !showAll)} aria-expanded={showAll}>
          {showAll ? `Show top ${DEFAULT_ROWS}` : `Show all ${rows.length}`}
        </button>
      {/if}
    </footer>
  </section>
{/if}

<style>
  .note {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem 1.1rem;
    max-width: 62ch;
  }
  .note h3 {
    margin: 0 0 0.35rem;
    font-size: 0.92rem;
  }
  .note p {
    margin: 0;
    color: var(--muted);
    font-size: 0.82rem;
    line-height: 1.55;
  }
  header {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    margin-bottom: 0.75rem;
  }
  h3 {
    margin: 0 0 0.2rem;
    font-size: 0.95rem;
  }
  /* the sub-board a metric like `elo:anime` names, kept quieter than the author */
  .cut {
    color: var(--muted);
    font-weight: 400;
  }
  .sub {
    margin: 0;
    color: var(--muted);
    font-size: 0.78rem;
    line-height: 1.5;
    max-width: 60ch;
  }
  .metric {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.78rem;
    color: var(--muted);
  }
  .metric select {
    padding: 0.25rem 0.4rem;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--bg);
    color: inherit;
    font: inherit;
    font-size: 0.8rem;
  }
  .bars {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 0.3rem;
  }
  .bars li {
    display: grid;
    grid-template-columns: 1.6rem minmax(9rem, 15rem) 1fr auto;
    gap: 0.6rem;
    align-items: center;
    font-size: 0.82rem;
    min-width: 0;
  }
  .rank {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    text-align: right;
    font-size: 0.75rem;
  }
  .who {
    min-width: 0;
  }
  .name {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .creator {
    display: block;
    color: var(--muted);
    font-size: 0.72rem;
  }
  .merged {
    opacity: 0.75;
    cursor: help;
  }
  .track {
    background: var(--panel2);
    border-radius: 3px;
    height: 0.65rem;
    min-width: 2rem;
    overflow: hidden;
  }
  .bar {
    display: block;
    height: 100%;
    background: var(--accent);
    border-radius: 3px;
  }
  .value {
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
    min-width: 3.5rem;
    text-align: right;
  }
  footer {
    display: flex;
    gap: 1rem;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    margin-top: 0.8rem;
  }
  .scale {
    color: var(--muted);
    font-size: 0.74rem;
  }
  footer button {
    border: 1px solid var(--line);
    border-radius: 6px;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 0.78rem;
    padding: 0.25rem 0.7rem;
    cursor: pointer;
  }
  @media (max-width: 560px) {
    .bars li {
      grid-template-columns: 1.4rem 1fr auto;
    }
    .track {
      grid-column: 2 / -1;
      grid-row: 2;
    }
  }
</style>
