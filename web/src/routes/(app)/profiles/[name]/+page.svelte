<script lang="ts">
  /**
   * One profile, tuned.
   *
   * A profile is its weights. Each axis is a share of the score, the shares add
   * to one, and moving one moves the others -- so the page renormalises on
   * every input and never shows a sum for somebody to manage. Under the weights
   * is the only other number: how many models to ship. Beside them is the list
   * those two produce, which is the whole answer.
   *
   * Everything else that used to be on this page -- requirements, a task shape,
   * a policy, pins, holds, a floor, a confidence, why-it-is-here bars, sources,
   * chips -- is gone. Each of them changed the answer without moving a slider,
   * and between them they made the sliders unreadable.
   *
   * WHAT IT WRITES
   *
   *   a weight, an axis, the ship count   PUT /v1/profiles/{name}/settings,
   *                                       400 ms after the last input
   *   Ship now                            POST /v1/profiles/{name}/apply
   *   copy · rename · delete              POST /v1/profiles · PATCH · DELETE
   *
   * The list comes from POST /v1/profiles/{name}/preview, which reweighs the
   * ranking the server already holds. It has three honest states and one
   * fact: ranking, the box is busy, it failed -- and "nothing ships", which
   * may only be said when an answer actually came back empty.
   */
  import { goto } from '$app/navigation';
  import {
    api,
    explainError,
    type ApiError,
    type AxisRow,
    type HistoryRow,
    type Listed,
    type ProfileSettings
  } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import { ago } from '$lib/freshness';
  import { runPulse } from '$lib/refresh.svelte';
  import { session } from '$lib/session.svelte';
  import {
    DEBOUNCE_MS,
    SHIP_MAX,
    SHIP_MIN,
    SLOW_MS,
    addAxis,
    barWidth,
    clampShip,
    listState,
    moveAxis,
    removeAxis,
    shipState
  } from '$lib/profile/tune';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  const name = $derived(data.name);

  let profile = $state<Profile | null>(null);
  let chain = $state<Chain | null>(null);
  let everyAxis = $state<AxisRow[]>([]);

  /** the tuning, as the page holds it: shares that add to one, and a length */
  let weights = $state<Record<string, number>>({});
  let ship = $state(4);

  let loading = $state(true);
  let gone = $state<ApiError | null>(null);
  let said = $state<{ ok: boolean; text: string } | null>(null);

  /* -- the list ---------------------------------------------------------- */

  let models = $state<Listed[] | null>(null);
  let next = $state<Listed[]>([]);
  let pending = $state(false);
  let waitedMs = $state(0);
  let failed = $state<string | null>(null);
  let showMore = $state(false);
  let shipping = $state(false);

  /* -- the menu ---------------------------------------------------------- */

  let menuOpen = $state(false);
  let asking = $state<'' | 'copy' | 'rename' | 'delete'>('');
  let askName = $state('');
  let history = $state<HistoryRow[] | null>(null);
  let adding = $state(false);

  /** ticks, so "shipped 12 min ago" keeps counting */
  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  const options = $derived({ token: session.token || undefined });

  const chosen = $derived(
    Object.keys(weights).sort(
      (a, b) => weights[b] - weights[a] || a.localeCompare(b)
    )
  );
  const meanings = $derived(
    Object.fromEntries(everyAxis.map((axis) => [axis.name, axis.meaning || axis.describes || '']))
  );
  const labels = $derived(
    Object.fromEntries(everyAxis.map((axis: AxisRow) => [axis.name, axis.label || axis.name]))
  );
  const spare = $derived(everyAxis.filter((axis: AxisRow) => !(axis.name in weights)));

  const shipped = $derived(chain ? [chain.primary, ...(chain.fallbacks ?? [])] : []);
  const listing = $derived(listState({ pending, waitedMs, error: failed, models }));
  const button = $derived(
    shipState({
      shipped,
      next: (models ?? []).map((row: Listed) => row.id),
      shipping,
      ready: listing === 'ready'
    })
  );

  /* ---------------------------------------------------------------------- */
  /* loading                                                                 */
  /* ---------------------------------------------------------------------- */

  $effect(() => {
    // a finished run re-ranks this seat; the list must follow
    runPulse.seen();
    const wanted = name;
    loading = true;
    models = null;
    failed = null;
    history = null;
    void (async () => {
      const [p, s, c] = await Promise.all([
        api.profile(wanted),
        api.profileSettings(wanted),
        api.chain(wanted)
      ]);
      if (wanted !== name) return;

      if (!p.ok || !p.value) {
        gone = p.ok
          ? { code: 'not_found', message: `There is no profile called ${wanted}.`, status: 404 }
          : p.error;
        loading = false;
        return;
      }
      gone = null;
      profile = p.value;
      chain = c.ok ? c.value : null;

      const held: ProfileSettings | null = s.ok && s.value?.weights ? s.value : null;
      weights = held ? { ...held.weights } : { ...p.value.weights };
      ship = clampShip(held ? held.ship : (p.value.ship ?? 4));
      loading = false;

      const axes = await api.axes(p.value.modality);
      if (wanted === name && axes.ok && Array.isArray(axes.value)) everyAxis = axes.value;

      await refresh();
    })();
  });

  /* ---------------------------------------------------------------------- */
  /* the list                                                                */
  /* ---------------------------------------------------------------------- */

  let timer: ReturnType<typeof setTimeout> | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let inflight = 0;

  /** Ask for the list these weights would ship. Debounced by the callers. */
  async function refresh(): Promise<void> {
    const wanted = name;
    const mine = ++inflight;
    pending = true;
    failed = null;
    waitedMs = 0;
    const started = Date.now();
    ticker ??= setInterval(() => (waitedMs = Date.now() - started), 500);

    const result = await api.preview(wanted, { weights, ship }, options);
    if (wanted !== name || mine !== inflight) return;

    if (ticker) {
      clearInterval(ticker);
      ticker = null;
    }
    pending = false;
    waitedMs = 0;
    if (!result.ok) {
      failed = explainError(result.error);
      return;
    }
    models = result.value?.models ?? [];
    next = result.value?.next ?? [];
  }

  /** A control moved: keep the page honest, then save and re-rank. */
  function touched(): void {
    said = null;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      void save();
      void refresh();
    }, DEBOUNCE_MS);
  }

  async function save(): Promise<void> {
    const wanted = name;
    const result = await api.saveProfileSettings(
      wanted,
      { weights, ship, remove_axes: everyAxis.map((a: AxisRow) => a.name).filter((a: string) => !(a in weights)) },
      options
    );
    if (wanted !== name) return;
    if (!result.ok) said = { ok: false, text: explainError(result.error) };
  }

  /* ---------------------------------------------------------------------- */
  /* the controls                                                            */
  /* ---------------------------------------------------------------------- */

  function move(axis: string, value: number): void {
    weights = moveAxis(weights, axis, value);
    touched();
  }

  function drop(axis: string): void {
    if (Object.keys(weights).length <= 1) {
      said = { ok: false, text: 'A profile is its weights; it needs at least one axis.' };
      return;
    }
    weights = removeAxis(weights, axis);
    touched();
  }

  function add(axis: string): void {
    weights = addAxis(weights, axis);
    adding = false;
    touched();
  }

  function setShip(value: number): void {
    const wanted = clampShip(value);
    if (wanted === ship) return;
    ship = wanted;
    touched();
  }

  /* ---------------------------------------------------------------------- */
  /* shipping                                                                */
  /* ---------------------------------------------------------------------- */

  async function shipNow(): Promise<void> {
    if (button.disabled) return;
    shipping = true;
    said = null;
    // the weights on screen have to be the weights on the box before it ships
    if (timer) clearTimeout(timer);
    await save();
    const result = await api.applyProfile(name, options);
    shipping = false;
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    chain = result.value?.chain ?? chain;
    now = new Date();
    const combo = (result.value?.combos ?? []).join(', ');
    said = { ok: true, text: combo ? `Shipped as ${combo}.` : 'Shipped.' };
    void refresh();
  }

  /* ---------------------------------------------------------------------- */
  /* the menu                                                                */
  /* ---------------------------------------------------------------------- */

  function openMenu(what: '' | 'copy' | 'rename' | 'delete'): void {
    menuOpen = false;
    asking = what;
    askName = what === 'copy' ? `${name}_copy` : name;
  }

  async function openHistory(): Promise<void> {
    menuOpen = false;
    if (history) {
      history = null;
      return;
    }
    const result = await api.history(name, options);
    history = result.ok && Array.isArray(result.value) ? result.value : [];
  }

  async function doCopy(): Promise<void> {
    const wanted = askName.trim();
    if (!wanted || !profile) return;
    const result = await api.newProfile(
      { name: wanted, modality: profile.modality, from: name, copy_from: name },
      options
    );
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    asking = '';
    await goto(`/profiles/${encodeURIComponent(wanted)}`);
  }

  async function doRename(): Promise<void> {
    const wanted = askName.trim();
    if (!wanted || wanted === name) {
      asking = '';
      return;
    }
    const result = await api.renameProfile(name, wanted, options);
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    asking = '';
    await goto(`/profiles/${encodeURIComponent(wanted)}`);
  }

  async function doDelete(): Promise<void> {
    let result = await api.removeProfile(name, false, options);
    if (!result.ok && result.error.code === 'in_use') {
      result = await api.removeProfile(name, true, options);
    }
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    await goto('/profiles');
  }
</script>

<svelte:head><title>{name} · Sieve</title></svelte:head>

<svelte:window
  onclick={(event) => {
    const target = event.target as HTMLElement | null;
    if (menuOpen && !target?.closest('.menu')) menuOpen = false;
  }}
/>

{#if loading}
  <p class="muted">Loading…</p>
{:else if gone}
  <h1 class="display name">{name}</h1>
  <p class="muted">{explainError(gone)}</p>
  <p><a href="/profiles">Back to the profiles</a></p>
{:else if profile}
  <header class="head">
    <div class="who">
      <h1 class="display name">{profile.name}</h1>
      <p class="purpose">{profile.purpose}</p>
    </div>
    <div class="acts">
      <button
        type="button"
        class="primary wide"
        disabled={button.disabled}
        title={button.title}
        onclick={shipNow}>{button.label}</button
      >
      <div class="menu">
        <button
          type="button"
          class="dots"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="More"
          onclick={() => (menuOpen = !menuOpen)}>···</button
        >
        {#if menuOpen}
          <div class="sheet" role="menu">
            <button type="button" role="menuitem" onclick={() => openMenu('copy')}>Copy</button>
            <button type="button" role="menuitem" onclick={() => openMenu('rename')}>Rename</button>
            <button type="button" role="menuitem" onclick={() => openMenu('delete')}>Delete</button>
            <button type="button" role="menuitem" onclick={openHistory}>History</button>
          </div>
        {/if}
      </div>
    </div>
  </header>

  {#if asking === 'copy' || asking === 'rename'}
    <div class="ask">
      <label>
        <span>{asking === 'copy' ? 'Copy to' : 'Rename to'}</span>
        <input bind:value={askName} spellcheck="false" autocomplete="off" />
      </label>
      <button
        type="button"
        class="primary"
        onclick={() => (asking === 'copy' ? doCopy() : doRename())}
        >{asking === 'copy' ? 'Copy' : 'Rename'}</button
      >
      <button type="button" class="quiet" onclick={() => (asking = '')}>Cancel</button>
    </div>
  {:else if asking === 'delete'}
    <div class="ask">
      <span>Delete {name}? The combo it ships stops being updated.</span>
      <button type="button" class="danger" onclick={doDelete}>Delete</button>
      <button type="button" class="quiet" onclick={() => (asking = '')}>Cancel</button>
    </div>
  {/if}

  {#if said}
    <p class:bad={!said.ok} class="said">{said.text}</p>
  {/if}

  <div class="columns">
    <div class="controls">
      <section class="panel weights">
        <h2>Weights</h2>
        <p class="sub">move one, the others follow</p>

        {#each chosen as axis (axis)}
          <div class="axis">
            <div class="axis-top">
              <div class="axis-who">
                <div class="axis-name">{labels[axis] ?? axis}</div>
                <div class="axis-meaning">{meanings[axis] ?? ''}</div>
              </div>
              <div class="axis-right">
                <span class="mono value">{weights[axis].toFixed(2)}</span>
                <button
                  type="button"
                  class="drop"
                  aria-label={`Remove ${labels[axis] ?? axis}`}
                  onclick={() => drop(axis)}>✕</button
                >
              </div>
            </div>
            <input
              class="slider"
              type="range"
              min="0"
              max="1"
              step="0.01"
              aria-label={labels[axis] ?? axis}
              value={weights[axis]}
              style:--share={barWidth(weights[axis])}
              oninput={(event) => move(axis, Number(event.currentTarget.value))}
            />
          </div>
        {/each}

        <div class="add">
          {#if adding}
            <select
              aria-label="Add an axis"
              onchange={(event) => {
                const picked = event.currentTarget.value;
                if (picked) add(picked);
              }}
            >
              <option value="">choose an axis…</option>
              {#each spare as axis (axis.name)}
                <option value={axis.name}>{axis.label || axis.name}</option>
              {/each}
            </select>
            <button type="button" class="quiet" onclick={() => (adding = false)}>Cancel</button>
          {:else}
            <button
              type="button"
              class="ghost"
              disabled={spare.length === 0}
              title={spare.length === 0 ? 'every axis for this modality is already weighted' : ''}
              onclick={() => (adding = true)}>+ Add an axis</button
            >
          {/if}
        </div>
      </section>

      <section class="panel howmany">
        <div>
          <h2>How many to ship</h2>
          <p class="sub">the first is used, the rest are fallbacks</p>
        </div>
        <div class="stepper">
          <button
            type="button"
            aria-label="One fewer"
            disabled={ship <= SHIP_MIN}
            onclick={() => setShip(ship - 1)}>−</button
          >
          <span class="display count">{ship}</span>
          <button
            type="button"
            aria-label="One more"
            disabled={ship >= SHIP_MAX}
            onclick={() => setShip(ship + 1)}>+</button
          >
        </div>
      </section>

      <button
        type="button"
        class="primary phone-ship"
        disabled={button.disabled}
        title={button.title}
        onclick={shipNow}>{button.label}</button
      >
    </div>

    <section class="panel list">
      <div class="list-head">
        <h2>What ships</h2>
        <span class="when">
          {#if chain}shipped {ago(new Date(chain.computed_at), now)}{:else}never shipped{/if}
        </span>
      </div>

      {#if listing === 'ranking'}
        <p class="state">Ranking…</p>
      {:else if listing === 'busy'}
        <p class="state">
          The box is ranking as fast as it can — every slot has been busy for
          {Math.round(SLOW_MS / 1000)} seconds.
          <button type="button" class="link" onclick={() => refresh()}>Try again</button>
        </p>
      {:else if listing === 'error'}
        <p class="state bad">
          {failed}
          <button type="button" class="link" onclick={() => refresh()}>Try again</button>
        </p>
      {:else if listing === 'empty'}
        <p class="state">Nothing ships: no model this box can reach scores on these weights.</p>
      {:else}
        {#each models ?? [] as row, index (row.id)}
          <div class="row">
            <span class="mono rank">{index + 1}</span>
            <div class="model-name">{row.name}</div>
            <div class="mono model-id">{row.local_ids[0] ?? row.id}</div>
            <div class="bar"><div class="fill" style:width={barWidth(row.score)}></div></div>
            <span class="mono number">{row.score.toFixed(2)}</span>
          </div>
        {/each}

        {#if next.length}
          {#each showMore ? next : next.slice(0, 2) as row, index (row.id)}
            <div class="row after" class:first={index === 0}>
              <span class="mono rank">{(models?.length ?? 0) + index + 1}</span>
              <div class="model-name">{row.name}</div>
              <div class="mono model-id">{row.local_ids[0] ?? row.id}</div>
              <div class="bar"><div class="fill" style:width={barWidth(row.score)}></div></div>
              <span class="mono number">{row.score.toFixed(2)}</span>
            </div>
          {/each}
          {#if next.length > 2}
            <button type="button" class="more" onclick={() => (showMore = !showMore)}
              >{showMore ? 'show fewer' : 'show more'}</button
            >
          {/if}
        {/if}
      {/if}
    </section>
  </div>

  {#if history}
    <section class="panel history">
      <div class="list-head">
        <h2>History</h2>
        <button type="button" class="link" onclick={() => (history = null)}>close</button>
      </div>
      {#if history.length === 0}
        <p class="state">Nothing has changed this profile yet.</p>
      {:else}
        <ul>
          {#each history.slice(0, 40) as row (row.when + row.what)}
            <li>
              <span class="mono when">{ago(new Date(row.when), now)}</span>
              <span class="what">{row.what}</span>
              <span class="who">{row.who}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}
{/if}

<style>
  .muted {
    color: var(--muted);
  }
  .display {
    font-family: var(--display);
    font-weight: 500;
  }
  .mono {
    font-family: var(--mono);
  }

  /* -- header ------------------------------------------------------------ */

  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 22px;
  }
  .who {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .name {
    font-size: 30px;
    line-height: 1.1;
    margin: 0;
  }
  .purpose {
    font-size: 14px;
    color: var(--muted);
    max-width: 62ch;
    margin: 0;
  }
  .acts {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  .primary {
    background: var(--accent);
    color: var(--bg);
    border: none;
    border-radius: 7px;
    padding: 9px 18px;
    font: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  .primary:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .menu {
    position: relative;
  }
  .dots {
    width: 36px;
    height: 36px;
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--muted);
    font: inherit;
    font-size: 16px;
    letter-spacing: 1px;
    cursor: pointer;
  }
  .sheet {
    position: absolute;
    right: 0;
    top: 42px;
    z-index: 5;
    display: flex;
    flex-direction: column;
    min-width: 9rem;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--panel);
    padding: 4px;
  }
  .sheet button {
    text-align: left;
    background: none;
    border: none;
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 8px 10px;
    border-radius: 7px;
    cursor: pointer;
  }
  .sheet button:hover {
    background: var(--panel2);
  }

  .ask {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--panel);
    padding: 12px 14px;
    margin-bottom: 16px;
    font-size: 13px;
  }
  .ask label {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
  }
  .ask input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 6px 9px;
  }
  .quiet,
  .danger {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 6px 12px;
    cursor: pointer;
  }
  .danger {
    border-color: var(--bad);
    color: var(--bad);
  }
  .said {
    font-size: 13px;
    color: var(--good);
    margin: 0 0 14px;
  }
  .said.bad {
    color: var(--bad);
  }

  /* -- the two columns --------------------------------------------------- */

  .columns {
    display: grid;
    grid-template-columns: 380px minmax(0, 1fr);
    gap: 22px;
    align-items: start;
  }
  .controls {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .panel {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 16px 18px;
  }
  .panel h2 {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
  }
  .sub {
    font-size: 12px;
    color: var(--muted);
    margin: 0 0 4px;
  }

  /* -- weights ----------------------------------------------------------- */

  .weights {
    display: flex;
    flex-direction: column;
  }
  .axis {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 14px 0;
    border-bottom: 1px solid var(--rule);
  }
  .axis-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
  }
  .axis-who {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .axis-name {
    font-size: 14px;
    font-weight: 600;
  }
  .axis-meaning {
    font-size: 12px;
    color: var(--muted);
  }
  .axis-right {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-shrink: 0;
  }
  .value {
    font-size: 14px;
  }
  .drop {
    background: none;
    border: none;
    color: var(--muted);
    font: inherit;
    font-size: 14px;
    padding: 0;
    cursor: pointer;
  }
  .drop:hover {
    color: var(--ink);
  }

  .slider {
    -webkit-appearance: none;
    appearance: none;
    width: 100%;
    height: 24px;
    background: transparent;
    cursor: pointer;
    margin: 0;
  }
  .slider::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 2px;
    /* the share this axis carries, filled from the left */
    background: linear-gradient(
      to right,
      var(--accent) 0 var(--share, 0%),
      var(--panel2) var(--share, 0%) 100%
    );
  }
  .slider::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: var(--panel2);
  }
  .slider::-moz-range-progress {
    height: 4px;
    border-radius: 2px;
    background: var(--accent);
  }
  .slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 18px;
    height: 18px;
    margin-top: -7px;
    border-radius: 50%;
    background: var(--ink);
    border: 2px solid var(--bg);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .slider::-moz-range-thumb {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--ink);
    border: 2px solid var(--bg);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .slider:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
  }

  .add {
    padding-top: 14px;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .ghost,
  .add select {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 13px;
    padding: 7px 12px;
    cursor: pointer;
  }
  .ghost:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  /* -- how many ---------------------------------------------------------- */

  .howmany {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .howmany .sub {
    margin: 2px 0 0;
  }
  .stepper {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .stepper button {
    width: 36px;
    height: 36px;
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font: inherit;
    font-size: 18px;
    cursor: pointer;
  }
  .stepper button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .count {
    font-size: 28px;
    width: 30px;
    text-align: center;
  }

  .phone-ship {
    display: none;
  }

  /* -- the list ---------------------------------------------------------- */

  .list {
    display: flex;
    flex-direction: column;
    padding-bottom: 14px;
  }
  .list-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 6px;
  }
  .when {
    font-size: 12px;
    color: var(--muted);
  }
  .state {
    font-size: 13px;
    color: var(--muted);
    padding: 14px 0 2px;
    border-top: 1px solid var(--rule);
    margin: 0;
  }
  .state.bad {
    color: var(--bad);
  }
  .link {
    background: none;
    border: none;
    color: var(--reach);
    font: inherit;
    font-size: 13px;
    padding: 0 0 0 6px;
    cursor: pointer;
  }

  .row {
    display: grid;
    grid-template-columns: 28px minmax(0, 1fr) 176px 34px;
    grid-template-areas:
      'rank name bar num'
      'rank id bar num';
    gap: 2px 16px;
    align-items: center;
    padding: 14px 0;
    border-top: 1px solid var(--rule);
  }
  .rank {
    grid-area: rank;
    font-size: 13px;
    color: var(--accent);
    align-self: center;
  }
  .model-name {
    grid-area: name;
    font-size: 16px;
    font-weight: 600;
    align-self: end;
    min-width: 0;
  }
  .model-id {
    grid-area: id;
    font-size: 11px;
    color: var(--muted);
    align-self: start;
    min-width: 0;
  }
  .bar {
    grid-area: bar;
    height: 8px;
    background: var(--panel2);
    border-radius: 4px;
    overflow: hidden;
  }
  .fill {
    height: 8px;
    background: var(--accent);
  }
  .number {
    grid-area: num;
    font-size: 12px;
    text-align: right;
  }

  .after {
    color: var(--muted);
    padding: 12px 0;
  }
  .after.first {
    border-top: 1px dashed var(--rule);
  }
  .after .rank {
    color: var(--muted);
  }
  .after .model-name {
    font-size: 15px;
    font-weight: 400;
  }
  .after .fill {
    background: var(--rule);
  }

  .more {
    text-align: left;
    background: none;
    border: none;
    border-top: 1px solid var(--rule);
    color: var(--reach);
    font: inherit;
    font-size: 13px;
    padding: 12px 0 2px;
    cursor: pointer;
  }

  /* -- history ----------------------------------------------------------- */

  .history {
    margin-top: 18px;
  }
  .history ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .history li {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr) 8rem;
    gap: 12px;
    padding: 10px 0;
    border-top: 1px solid var(--rule);
    font-size: 13px;
  }
  .history .when,
  .history .who {
    color: var(--muted);
    font-size: 12px;
  }

  /* -- the phone --------------------------------------------------------- */

  @media (max-width: 900px) {
    .columns {
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
    }
    .name {
      font-size: 26px;
    }
    .purpose {
      font-size: 13px;
    }
    .wide {
      display: none;
    }
    .phone-ship {
      display: block;
      width: 100%;
      min-height: 44px;
      padding: 12px;
      font-size: 15px;
    }
    .panel {
      padding: 14px;
    }
    .slider {
      height: 44px;
    }
    .slider::-webkit-slider-thumb {
      width: 24px;
      height: 24px;
      margin-top: -10px;
    }
    .slider::-moz-range-thumb {
      width: 24px;
      height: 24px;
    }
    .stepper button {
      width: 44px;
      height: 44px;
      font-size: 20px;
    }
    .count {
      font-size: 30px;
    }
    .ghost {
      width: 100%;
      min-height: 44px;
      text-align: center;
      font-size: 14px;
    }
    .row {
      grid-template-columns: 14px minmax(0, 1fr) auto;
      grid-template-areas:
        'rank name num'
        'rank bar bar'
        'rank id id';
      gap: 5px 12px;
      padding: 12px 0;
      align-items: baseline;
    }
    .rank {
      align-self: start;
    }
    .model-name {
      font-size: 15px;
      align-self: baseline;
    }
    .bar {
      height: 6px;
      border-radius: 3px;
    }
    .fill {
      height: 6px;
    }
    .number {
      font-size: 13px;
    }
    .history li {
      grid-template-columns: minmax(0, 1fr);
      gap: 2px;
    }
  }
</style>
