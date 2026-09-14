<script lang="ts">
  /**
   * The shell every screen sits in: one rail, one status box, one poll.
   *
   * The poll is the only thing on the page that talks to `/v1/status`, and it
   * changes pace: every 30 seconds normally, every 3 while a run is going, so
   * "running · 1m 20s" counts in something like real time without a page
   * hammering the API all day. When a run finishes, every list on whatever
   * screen you are on re-fetches itself -- that is what `runPulse` is for --
   * because the run has just rewritten the inventory, the rankings and the
   * chains under them.
   */
  import { api, type StatusRow } from '$lib/api/client';
  import Rail from '$lib/components/Rail.svelte';
  import StatusBox from '$lib/components/StatusBox.svelte';
  import { runPulse } from '$lib/refresh.svelte';
  import { session } from '$lib/session.svelte';

  let { children } = $props();

  let status = $state<StatusRow | null>(null);
  /** set when the status could not be fetched, so the rail says so */
  let unreachable = $state(false);

  const SLOW = 5 * 60_000;
  const QUICK = 3_000;

  /** Held outside `$state` on purpose: the poll reads them and must not
   * re-run the effect that owns it. */
  let runningId: string | null = null;
  let impatient = 0;

  let kick: () => void = () => {};

  $effect(() => {
    void session.refresh();
  });

  $effect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const refresh = async () => {
      const result = await api.status();
      if (!alive) return;
      unreachable = !result.ok;
      if (result.ok) {
        status = result.value;
        session.adopt(result.value);
        const going = result.value.runs?.running?.id ?? null;
        if (runningId && !going) {
          // it just finished: every screen is now looking at stale rows
          runPulse.bump();
          impatient = 0;
        }
        runningId = going;
        if (impatient > 0) impatient -= 1;
      }
      timer = setTimeout(refresh, runningId || impatient > 0 ? QUICK : SLOW);
    };

    kick = () => {
      // after Run now, or a schedule change: look again at once, and keep
      // looking often for a minute in case the run is short
      impatient = 20;
      clearTimeout(timer);
      void refresh();
    };

    void refresh();
    const onVisible = () => document.visibilityState === 'visible' && kick();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      alive = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  });
</script>

<div class="shell">
  <Rail {unreachable} />
  <div class="body">
    <StatusBox {status} token={session.token} onchanged={() => kick()} />
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
