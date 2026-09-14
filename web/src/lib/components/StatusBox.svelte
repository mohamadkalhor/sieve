<script lang="ts">
  /**
   * What the loop is doing, on every page, told truthfully.
   *
   * The box it replaces read `[schedule] pull` out of `sieve.toml` and said
   * "hourly · overdue" on a box whose timer had been overridden to 04:30
   * daily. It was not stale, it was wrong, and there was no way to run
   * anything from it.
   *
   * Four rows -- the full run and the three steps it is made of. Each says
   * when it last ran and what it did, when it is next due, how often it should
   * go, and has a button that makes it go now. While something is running the
   * row says so with the elapsed time, every button is disabled, and when it
   * finishes every list on the page re-fetches.
   */
  import {
    api,
    explainError,
    STEPS,
    STEP_LABEL,
    STEP_SAYS,
    type RunRow,
    type ScheduleRow,
    type StatusRow,
    type Step
  } from '$lib/api/client';
  import { ago } from '$lib/freshness';
  import { session } from '$lib/session.svelte';

  interface Props {
    status: StatusRow | null;
    /** a bearer token, for a browser with no gate session */
    token?: string;
    /** the caller polls harder for a while and re-fetches its lists */
    onchanged?: () => void;
  }
  let { status = null, token = '', onchanged }: Props = $props();

  const options = $derived({ token: token || undefined });

  /** ticks so "running · 1m 20s" counts up without another request */
  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 1000);
    return () => clearInterval(tick);
  });

  const running = $derived(status?.runs?.running ?? null);
  const schedules = $derived<ScheduleRow[]>(status?.schedules ?? []);
  const absent = $derived(status !== null && status.runs === undefined);

  let said = $state('');
  let busy = $state<string>('');

  function scheduleFor(step: Step): ScheduleRow | null {
    return schedules.find((s) => s.step === step) ?? null;
  }
  function lastFor(step: Step): RunRow | null {
    return status?.runs?.last_by_step?.[step] ?? null;
  }

  function elapsed(from: string, to: Date): string {
    const seconds = Math.max(0, Math.round((to.getTime() - new Date(from).getTime()) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }

  function when(stamp: string | null): string {
    if (!stamp) return '';
    const at = new Date(stamp);
    const sameDay = at.toDateString() === now.toDateString();
    return at.toLocaleString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      ...(sameDay ? {} : { month: 'short', day: 'numeric' })
    });
  }

  async function runNow(step: Step) {
    said = '';
    busy = step;
    const result = await api.startRun(step, options);
    busy = '';
    if (!result.ok) {
      said =
        result.error.code === 'run_in_flight'
          ? result.error.message
          : `${STEP_LABEL[step]}: ${explainError(result.error)}`;
      onchanged?.();
      return;
    }
    said = `${STEP_LABEL[step]} started.`;
    onchanged?.();
  }

  async function setMode(step: Step, mode: string) {
    const row = scheduleFor(step);
    said = '';
    busy = `mode:${step}`;
    const result = await api.saveSchedule(
      step,
      { mode, at_minute: row?.at_minute ?? 0, at_time: row?.at_time ?? '04:30' },
      options
    );
    busy = '';
    if (!result.ok) said = explainError(result.error);
    onchanged?.();
  }

  async function setAt(step: Step, field: 'at_minute' | 'at_time', raw: string) {
    const row = scheduleFor(step);
    if (!row) return;
    said = '';
    busy = `mode:${step}`;
    const body =
      field === 'at_minute'
        ? { mode: row.mode, at_minute: Math.max(0, Math.min(59, Number(raw) || 0)) }
        : { mode: row.mode, at_time: raw };
    const result = await api.saveSchedule(step, body, options);
    busy = '';
    if (!result.ok) said = explainError(result.error);
    onchanged?.();
  }

  const locked = $derived(!session.canWrite && !token);
</script>

<section class="box" aria-label="Runs">
  <header>
    <h2>The loop</h2>
    {#if running}
      <p class="going" role="status">
        <span class="spinner" aria-hidden="true"></span>
        {STEP_LABEL[running.step]} running · {elapsed(running.started, now)}
      </p>
    {:else if status?.runs?.last}
      <p class="quiet">
        last: {STEP_LABEL[status.runs.last.step]}
        {status.runs.last.ok ? 'ok' : 'failed'} · {ago(new Date(status.runs.last.finished ?? ''), now)}
      </p>
    {:else}
      <p class="quiet">nothing has run yet</p>
    {/if}
  </header>

  {#if absent}
    <p class="quiet">
      This server has no runs API yet. Nothing here can be trusted until it is deployed.
    </p>
  {:else}
    <ul>
      {#each STEPS as step (step)}
        {@const row = scheduleFor(step)}
        {@const last = lastFor(step)}
        {@const mine = running?.step === step}
        <li class:mine>
          <div class="what">
            <span class="name">{STEP_LABEL[step]}</span>
            <span class="says">{STEP_SAYS[step]}</span>
          </div>

          <div class="last">
            {#if mine && running}
              <span class="going"
                ><span class="spinner" aria-hidden="true"></span> running · {elapsed(
                  running.started,
                  now
                )}</span
              >
            {:else if last}
              <span class="mark" class:bad={!last.ok}>{last.ok ? '✓' : '✗'}</span>
              <span class="stamp">{when(last.finished)}</span>
              <span class="summary" title={last.error ?? ''}>{last.error ?? last.summary ?? ''}</span
              >
            {:else}
              <span class="summary quiet">never run</span>
            {/if}
          </div>

          <div class="next">
            {#if row?.next_fire}
              <span class="stamp">next {when(row.next_fire)}</span>
            {:else}
              <span class="quiet">no schedule</span>
            {/if}
          </div>

          <div class="cadence">
            <label class="sr" for={`mode-${step}`}>{STEP_LABEL[step]} schedule</label>
            <select
              id={`mode-${step}`}
              value={row?.mode ?? 'off'}
              disabled={locked || busy === `mode:${step}`}
              onchange={(e) => void setMode(step, e.currentTarget.value)}
            >
              <option value="off">off</option>
              <option value="hourly">hourly</option>
              <option value="daily">daily</option>
            </select>
            {#if row?.mode === 'hourly'}
              <label class="at">
                <span>min</span>
                <input
                  type="number"
                  min="0"
                  max="59"
                  step="1"
                  value={row.at_minute}
                  disabled={locked}
                  onchange={(e) => void setAt(step, 'at_minute', e.currentTarget.value)}
                />
              </label>
            {:else if row?.mode === 'daily'}
              <label class="at">
                <span class="sr">time</span>
                <input
                  type="time"
                  value={row.at_time}
                  disabled={locked}
                  onchange={(e) => void setAt(step, 'at_time', e.currentTarget.value)}
                />
              </label>
            {/if}
          </div>

          <button
            type="button"
            class="go"
            data-step={step}
            disabled={locked || running !== null || busy === step}
            onclick={() => void runNow(step)}
          >
            {busy === step ? 'Starting…' : 'Run now'}
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if said}
    <p class="said" role="status">{said}</p>
  {/if}
  {#if locked}
    <p class="quiet">
      Sign in, or paste a token with <code>profiles:write</code>, to run a step or change a
      schedule.
    </p>
  {/if}
</section>

<style>
  .box {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.6rem 0.75rem 0.7rem;
    margin-bottom: 1rem;
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  h2 {
    margin: 0;
    font-family: var(--ui);
    font-size: 0.9rem;
  }
  p {
    margin: 0;
    font-size: 0.74rem;
  }
  .quiet {
    color: var(--muted);
  }
  .going {
    color: var(--accent);
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .spinner {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    border: 2px solid var(--accent);
    border-top-color: transparent;
    display: inline-block;
    animation: spin 800ms linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 3s;
    }
  }
  ul {
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  li {
    display: grid;
    grid-template-columns: minmax(9rem, 1.1fr) minmax(0, 1.6fr) 8rem auto auto;
    gap: 0.5rem;
    align-items: center;
    padding: 0.3rem 0;
    border-top: 1px solid var(--rule);
    font-size: 0.76rem;
    min-width: 0;
  }
  li.mine {
    background: color-mix(in srgb, var(--accent) 7%, transparent);
  }
  .what {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .name {
    color: var(--ink);
  }
  .says {
    color: var(--muted);
    font-size: 0.68rem;
    overflow-wrap: anywhere;
  }
  .last,
  .next {
    display: flex;
    align-items: baseline;
    gap: 0.35rem;
    min-width: 0;
    color: var(--muted);
  }
  .mark {
    color: var(--good);
  }
  .mark.bad {
    color: var(--bad);
  }
  .stamp {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .summary {
    color: var(--muted);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cadence {
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }
  select,
  .at input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font: inherit;
    font-size: 0.74rem;
    padding: 0.15rem 0.3rem;
  }
  .at {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    color: var(--muted);
    font-size: 0.7rem;
  }
  .at input {
    width: 4.2rem;
  }
  /* a clock needs more room than two digits: it was showing `04:3` */
  .at input[type="time"] {
    width: 6.6rem;
  }
  .go {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.74rem;
    padding: 0.2rem 0.55rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .go:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
  .go:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .said {
    margin-top: 0.45rem;
    color: var(--accent);
  }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  code {
    font-family: var(--mono);
    font-size: 0.72rem;
  }

  @media (max-width: 900px) {
    li {
      grid-template-columns: minmax(0, 1fr) auto;
      row-gap: 0.2rem;
    }
    .what {
      grid-column: 1;
    }
    .go {
      grid-column: 2;
      grid-row: 1;
    }
    .last,
    .next,
    .cadence {
      grid-column: 1 / -1;
    }
  }
</style>
