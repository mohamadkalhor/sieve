<script lang="ts">
  /**
   * Pulse: what your own traffic says about the models you can reach.
   *
   * Every other screen reads what somebody else measured. This one reads what
   * happened when *you* called the model, which is the only evidence for the
   * health term in `final = score x health` — and the only thing that can tell
   * one effort mode's cost from another's, since every mode is billed at the
   * same rate per token.
   *
   * A model with no calls in the window is shown as "not called", never as
   * healthy. A flat line of ones would claim a model worked on a day nobody
   * tried it.
   */
  import { api, type ApiError, type StatusRow } from '$lib/api/client';
  import type { HealthRow } from '$lib/types';
  import Empty from '$lib/components/Empty.svelte';

  /**
   * Whether telemetry has *ever* arrived, and when it last did.
   *
   * An empty window is not an empty store. This screen used to answer both with
   * "nothing has reported a call yet", which was false for four days while the
   * store held thousands of calls that were simply older than a day.
   */
  let status = $state<StatusRow | null>(null);
  $effect(() => {
    api.status().then((result) => {
      if (result.ok) status = result.value;
    });
  });
  const lastCall = $derived(
    status?.telemetry_at
      ? new Date(status.telemetry_at).toLocaleString(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short'
        })
      : null
  );

  let rows = $state<HealthRow[]>([]);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);
  let window = $state<'24h' | '7d'>('24h');

  async function load() {
    loading = true;
    const result = await api.health(window);
    loading = false;
    if (result.ok) {
      rows = result.value;
      error = null;
    } else {
      error = result.error;
    }
  }

  $effect(() => {
    void window;
    void load();
  });

  const called = $derived(rows.filter((r) => (r.events ?? 0) > 0));
  const silent = $derived(rows.filter((r) => (r.events ?? 0) === 0));
  const totalEvents = $derived(called.reduce((sum, r) => sum + (r.events ?? 0), 0));

  /** Sort by health, worst first: this screen exists to surface trouble. */
  const ordered = $derived([...called].sort((a, b) => a.health - b.health));

  const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  const ms = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)} ms`);
  const num = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString());

  /** A sparkline as an inline polyline: null days leave a gap rather than a zero. */
  function points(series: (number | null)[]): string {
    const width = 84;
    const height = 20;
    const step = series.length > 1 ? width / (series.length - 1) : width;
    return series
      .map((v, i) => (v == null ? null : `${(i * step).toFixed(1)},${((1 - v) * height).toFixed(1)}`))
      .filter((p): p is string => p !== null)
      .join(' ');
  }

  const band = (health: number) => (health >= 0.95 ? 'good' : health >= 0.75 ? 'fair' : 'poor');
</script>

<svelte:head><title>Pulse · Sieve</title></svelte:head>

<header>
  <div>
    <h1>Pulse</h1>
    <p class="lede">
      What happened when you called them. Health is the only number here nothing else can
      supply — a benchmark says a model is good, this says it is answering you.
    </p>
  </div>
  <div class="windows" role="group" aria-label="Window">
    {#each ['24h', '7d'] as const as w (w)}
      <button
        type="button"
        class:on={window === w}
        aria-pressed={window === w}
        onclick={() => (window = w)}>{w}</button
      >
    {/each}
  </div>
</header>

{#if error}
  <Empty {error} title="Could not read /v1/health" />
{:else if loading && rows.length === 0}
  <p class="muted">Reading telemetry…</p>
{:else if called.length === 0 && lastCall}
  <Empty
    title={`No calls to a reachable model in the last ${window}`}
    hint={`The newest call on record is from ${lastCall} (${status?.telemetry_calls.toLocaleString()} held). Health only reflects calls inside the window, so until more arrive these models are judged on benchmarks alone.`}
  />
{:else if called.length === 0 && status}
  <Empty
    title="Nothing has reported a call yet"
    hint="Health stays at 1.00 until a gateway posts to /v1/telemetry. Until then every model is judged on benchmarks alone, which is what every other screen already shows."
  />
{:else if called.length === 0}
  <p class="muted">Reading telemetry…</p>
{:else}
  <p class="muted count">
    {totalEvents.toLocaleString()} call{totalEvents === 1 ? '' : 's'} across {called.length} model{called.length ===
    1
      ? ''
      : 's'}, last {window}.
  </p>

  <div class="scroll">
    <table>
      <caption class="sr-only">Model health and traffic over the last {window}</caption>
      <thead>
        <tr>
          <th scope="col">Model</th>
          <th scope="col">Health</th>
          <th scope="col">Trend</th>
          <th scope="col" class="n">OK</th>
          <th scope="col" class="n">Rate-limited</th>
          <th scope="col" class="n">p50</th>
          <th scope="col" class="n">p95</th>
          <th scope="col" class="n">Calls</th>
          <th scope="col" class="n">Tokens out</th>
        </tr>
      </thead>
      <tbody>
        {#each ordered as row (row.model_id)}
          <tr>
            <th scope="row">
              <span class="id">{row.model_id}</span>
              {#if row.local_ids?.length}
                <span class="local">{row.local_ids?.join(' · ')}</span>
              {/if}
            </th>
            <td><span class="health {band(row.health)}">{row.health.toFixed(2)}</span></td>
            <td>
              {#if points(row.series ?? [])}
                <svg viewBox="0 0 84 20" width="84" height="20" role="img"
                  aria-label="health over the last {(row.series ?? []).length} days">
                  <polyline points={points(row.series ?? [])} fill="none" stroke="currentColor"
                    stroke-width="1.5" stroke-linejoin="round" />
                </svg>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
            <td class="n">{pct(row.ok_rate)}</td>
            <td class="n" class:warn={(row.rate_limited_share ?? 0) > 0.05}>
              {pct(row.rate_limited_share)}
            </td>
            <td class="n">{ms(row.p50_latency_ms)}</td>
            <td class="n">{ms(row.p95_latency_ms)}</td>
            <td class="n">{(row.events ?? 0).toLocaleString()}</td>
            <td class="n">{num(row.median_tokens_out)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if silent.length}
    <p class="muted silent">
      {silent.length} reachable model{silent.length === 1 ? '' : 's'} reported no calls in this window,
      so {silent.length === 1 ? 'it is' : 'they are'} judged on benchmarks alone. Not called is not
      the same as healthy.
    </p>
  {/if}
{/if}

<style>
  header {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    margin-bottom: 0.5rem;
  }
  h1 {
    margin: 0 0 0.25rem;
    font-size: 1.35rem;
  }
  .lede {
    margin: 0;
    max-width: 56ch;
    color: var(--muted);
    font-size: 0.86rem;
    line-height: 1.5;
  }
  .windows {
    display: flex;
    gap: 0.25rem;
  }
  .windows button {
    border: 1px solid var(--line);
    background: transparent;
    color: inherit;
    padding: 0.3rem 0.7rem;
    border-radius: 999px;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  .windows button.on {
    background: var(--ink);
    color: var(--bg);
    border-color: var(--ink);
  }
  .count {
    margin: 0.75rem 0 0.5rem;
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85rem;
  }
  th,
  td {
    text-align: left;
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }
  thead th {
    font-weight: 600;
    color: var(--muted);
    font-size: 0.78rem;
  }
  tbody th {
    font-weight: 500;
  }
  .n {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .id {
    display: block;
  }
  .local {
    display: block;
    color: var(--muted);
    font-size: 0.75rem;
  }
  .health {
    font-variant-numeric: tabular-nums;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
  }
  .health.good {
    background: color-mix(in oklab, var(--ok, #2f855a) 16%, transparent);
  }
  .health.fair {
    background: color-mix(in oklab, #b7791f 20%, transparent);
  }
  .health.poor {
    background: color-mix(in oklab, #c53030 22%, transparent);
  }
  .warn {
    color: #b7791f;
  }
  .muted {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .silent {
    margin-top: 0.9rem;
    max-width: 62ch;
    line-height: 1.5;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
</style>
