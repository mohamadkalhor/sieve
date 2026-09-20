<script lang="ts">
  /**
   * One seat, as three panes (CONSOLE.md sections 5.2-5.4, 6.2-6.5): the list on
   * the left, the seat in the middle, the inspector on the right.
   *
   * The frame is this package's, and so is the session behind the middle pane.
   * The route owns it -- it creates it for the seat the URL names, hands it to
   * the pane, the inspector and the history drawer through context, and is the
   * only place that knows the seat can be left behind: `retarget` moves the one
   * session rather than replacing it, so the inspector keeps reading the same
   * object across a seat change.
   *
   * Each pane scrolls itself, so a long axis list never moves the column beside
   * it. Which panes are drawn is a media query, not a measurement: a stylesheet
   * gets it right on the first paint, without JavaScript, and cannot disagree
   * with itself halfway through a resize. Section 6.8's four widths come out of
   * `layout/viewport.svelte.ts` and must not drift from the three in the styles
   * below -- the responsive spec is what holds the two together.
   *
   * Below 1280px the inspector is not a column and becomes the overlay in
   * section 6.8: a drawer over the seat pane's right edge down to 900px, and a
   * bottom sheet under it from 900px down. The overlay is drawn from a
   * selection somebody *asked* for (see `state/selection.svelte.ts`), which is
   * the one thing here a stylesheet cannot know, and it is what stops a seat's
   * own answer from covering the seat on every load. The seats list (F) and the
   * inspector (E) are still the rows that say so; the seat pane, the history
   * drawer and the overlay are here.
   */
  import { browser } from '$app/environment';
  import { goto } from '$app/navigation';
  import { onDestroy, untrack } from 'svelte';
  import { page } from '$app/stores';
  import { palette, seats, provideSeatSession, provideSelection } from '$lib/console/context';
  import { attachSeat } from '$lib/console/state/palette.svelte';
  import SeatsPane from '$lib/console/seats/SeatsPane.svelte';
  import { browserDeps, SeatSession } from '$lib/console/state/seat.svelte';
  import { Selection } from '$lib/console/state/selection.svelte';
  import { viewport } from '$lib/console/layout/viewport.svelte';
  import Inspector from '$lib/console/inspector/Inspector.svelte';
  import InspectorOverlay from '$lib/console/inspector/InspectorOverlay.svelte';
  import HistoryDrawer from '$lib/console/seat/HistoryDrawer.svelte';
  import SeatPane from '$lib/console/seat/SeatPane.svelte';
  import type { PageData } from './$types';

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

  // The selection is URL state, not component state: `?model=` survives a
  // reload, and the palette opens a model in the inspector by writing it.
  const picked = new Selection({
    read: () => $page.url.searchParams.get('model'),
    replace: (id) => {
      const url = new URL($page.url);
      if (id) url.searchParams.set('model', id);
      else url.searchParams.delete('model');
      void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
    }
  });
  provideSelection(picked);

  // The table's view is URL state too, and for the same reason: `?view=all`
  // survives a reload, and a link to the whole pool is a link someone can send.
  // It is pushed rather than replaced, so Back returns to the lineup.
  const view = $derived($page.url.searchParams.get('view') === 'all' ? 'all' : 'lineup');

  function showView(next: 'lineup' | 'all'): void {
    const url = new URL($page.url);
    if (next === 'all') url.searchParams.set('view', 'all');
    else url.searchParams.delete('view');
    void goto(url, { keepFocus: true, noScroll: true });
  }

  // One session for the whole visit. A seat change moves it (see `retarget`)
  // instead of replacing it, which is what lets the inspector read one object.
  const session = new SeatSession(
    // read once: the effect below is what follows the URL, and a session that
    // rebuilt itself on every `here` would drop the answers it is waiting for
    untrack(() => here),
    browserDeps({
      patch: (name, part) => store.patch(name, part)
    })
  );
  provideSeatSession(session);

  // The palette runs against the seat that is open, and this route is the only
  // place that knows both the session and the selection it goes with (section
  // 5.2): without this, "Find a model" and "Ship" would be offered against
  // nothing.
  const commands = palette();
  $effect(() => {
    attachSeat(commands, {
      selection: picked,
      session: () => session,
      // the model's modality is the seat's, so the inspector reads it off the
      // session rather than off the command
      inspect: (id) => picked.select(id)
    });
    return () => attachSeat(commands, null);
  });

  // The single effect that opens a seat and closes the one before it.
  $effect(() => {
    const name = here;
    void session.retarget(name);
    // A selection was made in the seat it was made in.
    return () => picked.select(null);
  });

  // Once the seat has answered, the selection is checked against the pool that
  // answer names -- and it moves when the model it named has left the lineup.
  $effect(() => {
    if (session.preview) picked.adopt(session);
  });

  // Section 6.8, below 1280px: the inspector is an overlay rather than a column.
  // It is drawn when a person asked for a model -- a row, a palette hit, a
  // `?model=` link they followed -- and never for the selection `adopt` picked
  // on the seat's own behalf, which would otherwise cover the seat every load.
  const frame = viewport();
  const overlay = $derived(frame.shape !== 'three' && picked.wanted && picked.id !== null);
  const kind = $derived<'drawer' | 'sheet'>(frame.shape === 'single' ? 'sheet' : 'drawer');

  onDestroy(() => session.close());
</script>

<div class="work">
  <aside class="list" data-slot="seats" inert={overlay || undefined}>
    <SeatsPane />
  </aside>

  <section class="seat" data-slot="seat" aria-label={`Seat ${session.name}`} inert={overlay || undefined}>
    <SeatPane {session} {view} onview={showView} />
  </section>

  <aside class="inspect" data-slot="inspector" aria-label="Inspector" inert={overlay || undefined}>
    {#if session.historyOpen}
      <HistoryDrawer {session} />
    {:else}
      <div class="scroll">
        <Inspector {session} />
      </div>
    {/if}
  </aside>

  {#if overlay}
    <!-- a dialog covers part of the seat, so the seat stops answering: `inert`
         takes it out of the tab order, out of the pointer's reach and out of
         the accessibility tree while the panel is up -->
    <InspectorOverlay {session} {kind} onclose={() => picked.close()} />
  {/if}
</div>

<style>
  .work {
    display: grid;
    grid-template-columns: var(--w-seats) minmax(0, 1fr) var(--w-inspect);
    height: 100%;
    min-height: 0;
    /* the overlay in section 6.8 is placed against this box, not the page */
    position: relative;
  }

  .list,
  .seat,
  .inspect {
    min-width: 0;
    min-height: 0;
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

  /* section 6.8. 1280 and 1024 are the two widths in `layout/viewport.svelte.ts`
     that decide whether the inspector is a column: it is one from 1280 up, and
     below that it is the overlay the route mounts. 1024 is where the seats pane
     stops being worth 260px of a narrow window. */
  @media (max-width: 1279px) {
    .work {
      grid-template-columns: var(--w-seats) minmax(0, 1fr);
    }

    .inspect {
      display: none;
    }
  }

  @media (max-width: 1023px) {
    .work {
      grid-template-columns: minmax(0, 1fr);
    }

    .list {
      display: none;
    }

    .seat {
      border: 0;
    }
  }
</style>
