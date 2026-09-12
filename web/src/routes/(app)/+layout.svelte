<script lang="ts">
  import { api, type StatusRow } from '$lib/api/client';
  import Rail from '$lib/components/Rail.svelte';

  let { children } = $props();

  let status = $state<StatusRow | null>(null);
  /** set when the status could not be fetched, so the rail says so */
  let unreachable = $state(false);

  /*
    Fetched on load, every five minutes, and whenever the tab comes back into
    view -- so "updated 12 min ago" is true when you look at it, not when the
    page was first opened three hours ago in a background tab.

    This used to read `/v1/sources` and take the newest `last_pull`, which is a
    scan of every observation and did not move on a pull that found nothing
    new. `/v1/status` answers from the loop's own record.
  */
  $effect(() => {
    let alive = true;
    const refresh = () =>
      api.status().then((result) => {
        if (!alive) return;
        unreachable = !result.ok;
        if (result.ok) status = result.value;
      });
    refresh();
    const timer = setInterval(refresh, 5 * 60_000);
    const onVisible = () => document.visibilityState === 'visible' && refresh();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  });
</script>

<div class="shell">
  <Rail {status} {unreachable} />
  <main>{@render children()}</main>
</div>

<style>
  .shell {
    display: flex;
    min-height: 100dvh;
    align-items: stretch;
  }
  main {
    flex: 1;
    min-width: 0;
    padding: 1.5rem 1.75rem 4rem;
  }
  @media (max-width: 700px) {
    .shell {
      flex-direction: column;
    }
    main {
      padding: 1rem 0.9rem 3rem;
    }
  }
</style>
