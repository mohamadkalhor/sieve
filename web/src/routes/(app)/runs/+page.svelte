<script lang="ts">
  /**
   * Runs: what the loop has done, one row per run.
   *
   * The status box at the top of every page is the last few facts; this is the
   * record. Every run -- pressed by hand, fired by the schedule, or started
   * from a shell -- writes a row here with its own log, so "the 04:30 run
   * failed" is a thing you can read rather than a thing you deduce from a
   * ranking that did not move.
   */
  import { api, explainError, STEP_LABEL, type ApiError, type RunRow } from '$lib/api/client';
  import { ago } from '$lib/freshness';
  import { runPulse } from '$lib/refresh.svelte';
  import Empty from '$lib/components/Empty.svelte';
  import StatusBox from '$lib/components/StatusBox.svelte';
  import { session } from '$lib/session.svelte';

  let rows = $state<RunRow[]>([]);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);
  let absent = $state(false);

  let open = $state<string | null>(null);
  let log = $state('');
  let logLoading = $state(false);

  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 10_000);
    return () => clearInterval(tick);
  });

  async function load() {
    const result = await api.runs(50);
    loading = false;
    // A route this server does not have comes back as the SPA shell: a 200
    // with a body that is not an array.
    if ((!result.ok && result.error.status === 404) || (result.ok && !Array.isArray(result.value))) {
      absent = true;
      rows = [];
      return;
    }
    if (!result.ok) {
      error = result.error;
      return;
    }
    absent = false;
    error = null;
    rows = result.value;
  }

  $effect(() => {
    // re-reads itself when a run finishes anywhere in the app
    runPulse.seen();
    void load();
  });

  /** a run that is still going: keep the list moving while it does */
  const live = $derived(rows.some((r) => r.running));
  $effect(() => {
    if (!live) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  });

  async function show(run: RunRow) {
    if (open === run.id) {
      open = null;
      return;
    }
    open = run.id;
    log = '';
    logLoading = true;
    const result = await api.runLog(run.id);
    logLoading = false;
    log = result.ok ? result.value : explainError(result.error);
  }

  function took(run: RunRow): string {
    const seconds = run.seconds;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  }
</script>

<svelte:head><title>Runs · Sieve</title></svelte:head>

<StatusBox token={session.token} />

<h1>Runs</h1>
<p class="lede">
  Every time the loop went, and what it did. Start one from the box above; the schedule fires the
  same steps through the same path, so a scheduled run and a hand-pressed one differ only in who
  asked for it.
</p>

{#if absent}
  <p class="banner" role="status">This server has no runs API yet.</p>
{:else if error}
  <Empty error={error} title="Runs" hint="The API would not say what has run." />
{:else if loading}
  <p class="muted">Loading…</p>
{:else if rows.length === 0}
  <p class="muted">Nothing has run yet. Press Run now above and this fills in.</p>
{:else}
  <ul class="runs">
    {#each rows as run (run.id)}
      <li class:running={run.running} class:bad={run.ok === false}>
        <button type="button" class="row" aria-expanded={open === run.id} onclick={() => void show(run)}>
          <span class="mark" aria-hidden="true">
            {#if run.running}·{:else if run.ok}✓{:else}✗{/if}
          </span>
          <span class="step">{STEP_LABEL[run.step] ?? run.step}</span>
          <span class="when">{ago(new Date(run.started), now)}</span>
          <span class="took num">{took(run)}</span>
          <span class="who">{run.requested_by}</span>
          <span class="summary">{run.error ?? run.summary ?? (run.running ? 'running…' : '')}</span>
        </button>
        {#if open === run.id}
          <pre class="log">{logLoading ? 'Loading…' : log}</pre>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<style>
  h1 {
    margin: 0 0 0.2rem;
    font-size: 1.6rem;
  }
  .lede {
    margin: 0 0 1rem;
    color: var(--muted);
    font-size: 0.8rem;
    max-width: 46rem;
  }
  .muted,
  .banner {
    color: var(--muted);
    font-size: 0.8rem;
  }
  .runs {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: hidden;
  }
  li + li {
    border-top: 1px solid var(--rule);
  }
  .row {
    display: grid;
    grid-template-columns: 1.2rem minmax(7rem, auto) 7rem 5rem minmax(6rem, auto) minmax(0, 1fr);
    gap: 0.6rem;
    align-items: baseline;
    width: 100%;
    box-sizing: border-box;
    background: none;
    border: 0;
    color: inherit;
    font: inherit;
    font-size: 0.78rem;
    text-align: left;
    padding: 0.45rem 0.7rem;
    cursor: pointer;
  }
  .row:hover {
    background: var(--panel2);
  }
  .mark {
    color: var(--good);
  }
  li.bad .mark {
    color: var(--bad);
  }
  li.running .mark {
    color: var(--accent);
  }
  .step {
    color: var(--ink);
  }
  .when,
  .took,
  .who {
    color: var(--muted);
    white-space: nowrap;
  }
  .summary {
    color: var(--muted);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .log {
    margin: 0;
    padding: 0.6rem 0.8rem;
    background: var(--bg);
    border-top: 1px solid var(--rule);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 0.72rem;
    line-height: 1.5;
    max-height: 24rem;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  @media (max-width: 900px) {
    .row {
      grid-template-columns: 1.2rem minmax(0, 1fr) auto;
      row-gap: 0.15rem;
    }
    .when {
      text-align: right;
    }
    .took,
    .who {
      display: none;
    }
    .summary {
      grid-column: 2 / -1;
      white-space: normal;
    }
  }
</style>
