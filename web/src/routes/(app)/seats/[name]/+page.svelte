<script lang="ts">
  /**
   * One seat, as three panes (CONSOLE.md sections 6.2, 6.3, 6.4): the list on
   * the left, the seat in the middle, the inspector on the right.
   *
   * The frame is this package's. Each pane scrolls itself, so a long axis list
   * never moves the column beside it, and below 900px there is one column and
   * the seat is it -- the list is `/seats`, one tap away.
   *
   * The bodies arrive with the packages that own them: the seat pane (its
   * header, its axes, the preview, the ship) with D, the inspector with E, and
   * both through F's stores. What is here now is the frame and the two rows
   * that say so, so the routing, the widths and the scrolling are settled
   * before the panes land in them.
   */
  import { browser } from '$app/environment';
  import { page } from '$app/stores';
  import type { PageData } from './$types';
  import { explainError } from '$lib/api/client';
  import { seats } from '$lib/console/context';

  let { data }: { data: PageData } = $props();

  const store = seats();
  const here = $derived(decodeURIComponent($page.params.name ?? data.name));

  $effect(() => {
    if (!store.rows) void store.load();
  });

  $effect(() => {
    // §1.3: opening a seat is what makes `/seats` open it again next time, so
    // the key is written here rather than when the list is drawn.
    if (browser) window.localStorage.setItem('sieve:last-seat', here);
  });
</script>

<div class="work">
  <aside class="list" data-slot="seats" aria-label="Seats">
    <div class="scroll">
      {#if store.error}
        <p class="quiet">{explainError(store.error)}</p>
      {:else if !store.rows}
        <p class="quiet">Reading the seats…</p>
      {:else}
        <ul class="rows">
          {#each store.rows as row (row.name)}
            <li>
              <a
                class="row"
                href={`/seats/${encodeURIComponent(row.name)}`}
                aria-current={row.name === here ? 'page' : undefined}
              >
                <span class="name mono">{row.name}</span>
                {#if row.changes}
                  <span class="changes num">{row.changes}</span>
                {/if}
              </a>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </aside>

  <section class="seat" data-slot="seat" aria-label={`Seat ${here}`}>
    <div class="scroll">
      <h1 class="mono">{here}</h1>
      <p class="quiet">
        The seat pane — what it is for, every axis, what would change if you shipped it, and the
        button — arrives with the seat package.
      </p>
    </div>
  </section>

  <aside class="inspect" data-slot="inspector" aria-label="Inspector">
    <div class="scroll">
      <p class="quiet">The inspector arrives with the inspector package. Select a model to open it.</p>
    </div>
  </aside>
</div>

<style>
  .work {
    display: grid;
    grid-template-columns: var(--w-seats) minmax(0, 1fr) var(--w-inspect);
    height: 100%;
    min-height: 0;
  }

  .list,
  .seat,
  .inspect {
    min-width: 0;
  }

  .list {
    border-right: 1px solid var(--c-rule);
  }

  .inspect {
    border-left: 1px solid var(--c-rule);
  }

  /* each pane keeps its own place in the page, and scrolls under itself */
  .scroll {
    height: 100%;
    overflow: auto;
    padding: 16px;
  }

  .list .scroll {
    padding: 10px 8px;
  }

  h1 {
    margin: 0 0 10px;
    font-size: 15px;
    font-weight: 500;
    color: var(--c-ink);
  }

  .rows {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--r-3);
    font-size: 13px;
    color: var(--c-ink-2);
  }

  .row:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  .row[aria-current='page'] {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .changes {
    margin-left: auto;
    font-size: 11px;
    color: var(--c-accent);
  }

  .quiet {
    margin: 0 0 10px;
    font-size: 13px;
    color: var(--c-muted);
    max-width: 62ch;
  }

  @media (max-width: 900px) {
    .work {
      grid-template-columns: minmax(0, 1fr);
    }

    .list,
    .inspect {
      display: none;
    }

    .seat {
      border: 0;
    }
  }
</style>
