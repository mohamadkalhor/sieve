<script lang="ts">
  /**
   * The seats pane (CONSOLE.md section 6.2): every seat, by modality, on the
   * left of the seats list and of the open seat alike.
   *
   * It reads one store and paints it, and it asks for the list only when nobody
   * else has: below 900px this pane is the whole screen, and a screen that waits
   * for a request another screen would have made shows six grey rows forever.
   * Two callers are one request -- `SeatsStore.load` joins the one in flight
   * (CONSOLE.md section 5.3), which is what makes the pane's "one request"
   * acceptance true rather than lucky.
   *
   * Which groups are collapsed is this pane's own view state, kept in
   * localStorage rather than in the URL: it is how somebody arranges their own
   * screen, not a place they can send to another person.
   */
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { explainError } from '$lib/api/client';
  import { seats as seatsStore } from '../context';
  import { groupByModality } from '../logic/seats';
  import Popover from '../ui/Popover.svelte';
  import Skeleton from '../ui/Skeleton.svelte';
  import NewSeatForm from './NewSeatForm.svelte';
  import SeatLink from './SeatLink.svelte';
  import { COLLAPSED_KEY, parseCollapsed, toggled } from './view';

  const store = seatsStore();
  /** the seat this route has open, if it has one */
  const here = $derived($page.params.name ?? '');
  /** `/seats?new=1`: where the palette's "New seat" lands */
  const wantsNew = $derived($page.url.searchParams.get('new') === '1');
  const groups = $derived(store.rows ? groupByModality(store.rows) : []);

  let collapsed = $state<string[]>([]);
  let formOpen = $state(false);
  let anchor = $state<HTMLElement | null>(null);

  onMount(() => {
    collapsed = parseCollapsed(localStorage.getItem(COLLAPSED_KEY));
  });

  // Nobody has asked yet: this is the screen that needs the answer. A failure
  // stops here rather than looping -- the error state's Retry is the way back.
  $effect(() => {
    if (store.rows === null && !store.loading && !store.error) void store.load();
  });

  $effect(() => {
    if (wantsNew) formOpen = true;
  });

  function toggleGroup(key: string): void {
    collapsed = toggled(collapsed, key);
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify(collapsed));
    } catch {
      // a browser that refuses storage is not a reason to stop drawing the list
    }
  }

  function close(): void {
    formOpen = false;
    // the form's address is a place that opens it: leaving has to leave it
    if (wantsNew) void goto($page.url.pathname, { replaceState: true, noScroll: true });
  }
</script>

<aside class="pane" aria-label="Seats">
  <div class="list">
    {#if store.error}
      <p class="note bad">{explainError(store.error)}</p>
      <button class="retry" type="button" onclick={() => void store.load()}>Retry</button>
    {:else if store.rows === null}
      <Skeleton rows={6} height={46} />
    {:else if store.degraded}
      <p class="note">This server cannot say which seats are in step.</p>
    {/if}

    {#if store.rows && groups.length === 0}
      <p class="note">No seats yet.</p>
    {/if}

    {#each groups as group (group.modality)}
      {@const key = group.modality}
      {@const closed = collapsed.includes(key)}
      <section class="group">
        <h2>
          <button
            class="head"
            type="button"
            aria-expanded={!closed}
            onclick={() => toggleGroup(key)}
          >
            <span class="caps">{group.label}</span>
            <span class="count">{group.rows.length}</span>
          </button>
        </h2>
        {#if !closed}
          {#each group.rows as row (row.name)}
            <SeatLink {row} selected={row.name === here} />
          {/each}
        {/if}
      </section>
    {/each}
  </div>

  <div class="foot">
    <button class="new" type="button" bind:this={anchor} onclick={() => (formOpen = true)}>
      New seat
    </button>
    <Popover open={formOpen} {anchor} onclose={close} label="New seat" width={300}>
      <NewSeatForm
        oncreated={(name) => void goto(`/seats/${encodeURIComponent(name)}`)}
      />
    </Popover>
  </div>
</aside>

<style>
  .pane {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    padding: 10px 8px;
  }
  .list {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
  .group {
    margin-bottom: 6px;
  }
  h2 {
    margin: 0;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    height: 22px;
    padding: 0 8px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-muted);
    cursor: pointer;
  }
  .head:hover {
    background: var(--c-panel);
    color: var(--c-ink);
  }
  .head:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: -2px;
  }
  .count {
    font-family: var(--f-mono);
    font-size: 11px;
  }
  .note {
    margin: 8px;
    color: var(--c-muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .note.bad {
    color: var(--c-bad);
  }
  .retry {
    margin: 0 8px;
    height: 26px;
    padding: 0 10px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink);
    font-family: var(--f-ui);
    font-size: 12px;
    cursor: pointer;
  }
  .foot {
    flex: none;
    padding: 8px 4px 0;
  }
  .new {
    width: 100%;
    height: 32px;
    border: 1px dashed var(--c-rule-strong);
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-muted);
    font-family: var(--f-ui);
    font-size: 13px;
    cursor: pointer;
  }
  .new:hover {
    border-color: var(--c-rule-hover);
    color: var(--c-ink);
  }
  .new:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: 1px;
  }
</style>
