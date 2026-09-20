<script lang="ts">
  /**
   * The seats list, and the question a desktop should not have to ask twice
   * (CONSOLE.md sections 6.1 and 6.2).
   *
   * On a desktop `/seats` is not a screen: it opens the seat you last worked
   * in, else the first seat with changes waiting, else the first seat. "Which
   * one?" is a question the console can answer, and a click to answer it is a
   * click on every visit. Below 900px there is no room for a list beside a
   * seat, so there the list is the screen -- and the list is the pane, drawn
   * once and used in both places rather than copied into this file.
   *
   * `?new=1` is the palette's "New seat": it asks for the form, not for a seat,
   * so the seat it opens carries the query along and the pane reads it there.
   */
  import { browser } from '$app/environment';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { seats } from '$lib/console/context';
  import SeatsPane from '$lib/console/seats/SeatsPane.svelte';

  /** §1.3's key: the seat page writes it, this page reads it, so a return
   * visit opens where you left off rather than asking again */
  const LAST_SEAT = 'sieve:last-seat';

  const store = seats();

  /** false once the list is the answer: nothing to open, or a narrow screen */
  let opening = $state(true);
  /** the palette's "New seat", which is a request for the form */
  const wantsNew = $derived($page.url.searchParams.get('new') === '1');

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
      // Nothing to open, or nothing readable: stay here and let the pane say
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
      const query = wantsNew ? '?new=1' : '';
      await goto(`/seats/${encodeURIComponent(seat.name)}${query}`, { replaceState: true });
    })();
    return () => {
      alive = false;
    };
  });
</script>

<div class="screen">
  <header class="head">
    <h1>Seats</h1>
    <p class="say">
      Every seat: what it is for, what it holds now, and what it would hold if you shipped it.
    </p>
  </header>

  {#if opening}
    <p class="quiet">Opening the seat you last worked in…</p>
  {:else}
    <SeatsPane />
  {/if}
</div>

<style>
  /* the stage is flush here: the seat route is panes that scroll themselves,
     and this screen is the pane */
  .screen {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    height: 100%;
    min-height: 0;
    padding: 20px 28px 64px;
  }

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

  .quiet {
    margin: 0;
    font-size: 13px;
    color: var(--c-muted);
  }

  @media (max-width: 900px) {
    .screen {
      padding: 12px 12px 48px;
    }

    .head {
      margin-bottom: 12px;
    }
  }
</style>
