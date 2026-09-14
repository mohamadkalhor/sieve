<script lang="ts">
  /**
   * The shell every screen sits in: one rail, and one question.
   *
   * The status box used to be here, which put a control panel above every
   * screen and made every screen poll `/v1/status` for ever. It belongs to the
   * two screens that are about the loop -- Connectors and Runs -- and it does
   * its own polling now, so a page without the box makes no repeating request
   * at all.
   *
   * What is left here is the one thing every screen needs: who is signed in,
   * asked once, and whether the API answered at all, so the rail can say so.
   */
  import { api } from '$lib/api/client';
  import Rail from '$lib/components/Rail.svelte';
  import { session } from '$lib/session.svelte';

  let { children } = $props();

  /** set when the status could not be fetched, so the rail says so */
  let unreachable = $state(false);

  $effect(() => {
    void session.refresh();
  });

  $effect(() => {
    let alive = true;
    void (async () => {
      const result = await api.status();
      if (!alive) return;
      unreachable = !result.ok;
      if (result.ok) session.adopt(result.value);
    })();
    return () => {
      alive = false;
    };
  });
</script>

<div class="shell">
  <Rail {unreachable} />
  <div class="body">
    <main>{@render children()}</main>
  </div>
</div>

<style>
  .shell {
    display: flex;
    min-height: 100dvh;
    align-items: stretch;
  }
  .body {
    flex: 1;
    min-width: 0;
    padding: 1.25rem 1.75rem 4rem;
  }
  main {
    min-width: 0;
  }
  @media (max-width: 900px) {
    .shell {
      flex-direction: column;
    }
    .body {
      padding: 1rem 0.9rem 3rem;
    }
  }
</style>
