<script lang="ts">
  /**
   * The seats list, and the question a desktop should not have to ask twice
   * (CONSOLE.md sections 6.1 and 6.2).
   *
   * On a desktop `/seats` is not a screen: it opens the seat you last worked
   * in, else the first seat with changes waiting, else the first seat. "Which
   * one?" is a question the console can answer, and a click to answer it is a
   * click on every visit. Below 900px there is no room for a list beside a
   * seat, so there the list is the screen and this is it.
   *
   * The rows come from the seats store, which really fetches: this page has to
   * answer from the server's list, not from a copy of it. The filters, counts
   * and sort, and the pane's own look, arrive with the seats package.
   */
  import { browser } from '$app/environment';
  import { goto } from '$app/navigation';
  import { explainError } from '$lib/api/client';
  import { seats } from '$lib/console/context';
  import Icon from '$lib/console/ui/Icon.svelte';

  /** §1.3's key: the seat page writes it, this page reads it, so a return
   * visit opens where you left off rather than asking again */
  const LAST_SEAT = 'sieve:last-seat';

  const store = seats();

  /** false once the list is the answer: nothing to open, or a narrow screen */
  let opening = $state(true);

  $effect(() => {
    if (!browser) return;
    if (!window.matchMedia('(min-width: 900px)').matches) {
      opening = false;
      return;
    }
    let alive = true;
    void (async () => {
      await store.load();
      if (!alive) return;
      const rows = store.rows ?? [];
      // Nothing to open, or nothing readable: stay here and let the page say
      // which of the two it is.
      if (!rows.length) {
        opening = false;
        return;
      }
      const remembered = window.localStorage.getItem(LAST_SEAT);
      const seat =
        rows.find((row) => row.name === remembered) ??
        rows.find((row) => (row.changes ?? 0) > 0) ??
        rows[0];
      await goto(`/seats/${encodeURIComponent(seat.name)}`, { replaceState: true });
    })();
    return () => {
      alive = false;
    };
  });
</script>

<header class="head">
  <h1>Seats</h1>
  <p class="say">
    Every seat: what it is for, what it holds now, and what it would hold if you shipped it.
  </p>
</header>

{#if store.error}
  <p class="bad">{explainError(store.error)}</p>
{:else if !store.rows}
  <p class="quiet">{opening ? 'Opening the seat you last worked in…' : 'Reading the seats…'}</p>
{:else if !store.rows.length}
  <p class="quiet">No seats yet.</p>
{:else}
  {#if store.degraded}
    <p class="warn note caps">
      Some chains could not be read, so this list is provisional.
    </p>
  {/if}
  <ul class="rows">
    {#each store.rows as row (row.name)}
      <li>
        <a class="row" href={`/seats/${encodeURIComponent(row.name)}`}>
          <span class="name mono">{row.name}</span>
          <span class="caps">{row.modality}</span>
          {#if row.changes}
            <span class="changes num">{row.changes} changed</span>
          {/if}
          <Icon name="chevron" />
        </a>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .head {
    margin: 0 0 18px;
  }

  h1 {
    margin: 0;
    font-size: 20px;
    font-weight: 500;
    color: var(--c-ink);
  }

  .say {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--c-muted);
    max-width: 62ch;
  }

  .rows {
    margin: 0;
    padding: 0;
    list-style: none;
    border-top: 1px solid var(--c-rule);
  }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 6px;
    border-bottom: 1px solid var(--c-rule);
    color: var(--c-ink-2);
  }

  .row:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  .name {
    font-size: 13px;
    color: inherit;
  }

  .changes {
    font-size: 12px;
    color: var(--c-accent);
  }

  .warn {
    color: var(--c-warn);
  }

  .row :global(svg) {
    margin-left: auto;
    color: var(--c-muted);
  }

  .bad {
    margin: 0;
    font-size: 13px;
    color: var(--c-bad);
  }

  .quiet {
    margin: 0;
    font-size: 13px;
    color: var(--c-muted);
  }

  @media (max-width: 900px) {
    .head {
      margin-bottom: 12px;
    }

    .row {
      gap: 8px;
      padding: 12px 6px;
    }
  }
</style>
