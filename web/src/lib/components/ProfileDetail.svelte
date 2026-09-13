<script lang="ts">
  /**
   * The rest of a profile, opened by clicking its card.
   *
   * The row above holds the handful of numbers somebody moves every day. This
   * holds the ones they move once a quarter and then want to see: every axis
   * with the room it may move in, the confidence floor, what a local id costs
   * relative to its list price, the per-axis detail the Rankings screen used to
   * be a whole page for, who changed what, and how the models have actually
   * behaved when called.
   *
   * It writes nothing on its own. Every edit lands in the same draft the row
   * holds, and the row's Apply is what sends it -- so there is one Apply per
   * profile and not one per panel.
   */
  import {
    api,
    explainError,
    type ExperienceRow,
    type HistoryRow,
    type ProfileSettings
  } from '$lib/api/client';
  import type { Profile, Ranking } from '$lib/types';
  import AxisBars from '$lib/components/AxisBars.svelte';
  import ConfDots from '$lib/components/ConfDots.svelte';
  // aliased: `ProfileSettings` is already the name of the settings *shape*
  import SettingsBlocks from '$lib/components/ProfileSettings.svelte';
  import WeightSlider from '$lib/components/WeightSlider.svelte';

  interface Props {
    profile: Profile;
    /** the row's draft, mutated in place: the row's Apply is what saves it */
    draft: ProfileSettings;
    settingsAbsent: boolean;
    ranking: Ranking | null;
    token: string;
    defaults: Record<string, number> | null;
    onchange: () => void;
    onsaved: (message: string) => void;
    onfailed: (message: string) => void;
    onchanged: (message: string) => void;
  }
  let {
    profile,
    draft,
    settingsAbsent,
    ranking,
    token,
    defaults,
    onchange,
    onsaved,
    onfailed,
    onchanged
  }: Props = $props();

  const options = $derived({ token: token || undefined });

  let history = $state<HistoryRow[]>([]);
  let historyNote = $state('');
  let experience = $state<ExperienceRow[]>([]);
  let experienceNote = $state('');

  /** the rename box; refilled whenever the row is showing a different profile */
  let renaming = $state('');
  $effect(() => {
    renaming = profile.name;
  });
  let confirming = $state(false);
  let busy = $state('');

  /**
   * History, and where it comes from when the profile route is absent.
   *
   * `/v1/decisions?profile=` has recorded every weight change, hold and apply
   * since phase 1, in all but the field names -- so the panel shows that
   * rather than an empty box, and says which one it read.
   */
  $effect(() => {
    const wanted = profile.name;
    void (async () => {
      const result = await api.history(wanted);
      if (wanted !== profile.name) return;
      if (result.ok && Array.isArray(result.value)) {
        history = result.value;
        historyNote = '';
        return;
      }
      const decisions = await api.decisions(wanted);
      if (wanted !== profile.name) return;
      if (decisions.ok && Array.isArray(decisions.value)) {
        history = decisions.value.map((decision) => ({
          who: decision.actor,
          when: decision.at,
          what: `${decision.kind}: ${decision.reason}`,
          before: decision.before,
          after: decision.after
        }));
        historyNote = 'read from the decision log, until this profile has a history route';
      } else {
        history = [];
        historyNote = 'nothing recorded yet';
      }
    })();
  });

  $effect(() => {
    const wanted = profile.name;
    void (async () => {
      const result = await api.experience(wanted);
      if (wanted !== profile.name) return;
      if (result.ok && Array.isArray(result.value)) {
        experience = result.value;
        experienceNote = result.value.length ? '' : 'nothing called on this seat yet';
        return;
      }
      experience = [];
      experienceNote = 'this server has no experience route yet';
    })();
  });

  /* ---------------------------------------------------------------------- */
  /* axes                                                                    */
  /* ---------------------------------------------------------------------- */

  const axes = $derived(Object.entries(draft.weights).sort(([a], [b]) => a.localeCompare(b)));

  function bound(axis: string, which: 'min' | 'max', value: number) {
    const control = draft.weights[axis];
    if (!control || !Number.isFinite(value)) return;
    control[which] = Math.min(1, Math.max(0, value));
    if (control.min > control.max) control[which === 'min' ? 'max' : 'min'] = control[which];
    onchange();
  }

  function setValue(axis: string, value: number) {
    const control = draft.weights[axis];
    if (!control) return;
    control.value = value;
    onchange();
  }

  /* ---------------------------------------------------------------------- */
  /* cost multipliers                                                        */
  /* ---------------------------------------------------------------------- */

  const prefixes = $derived(
    [...new Set([...Object.keys(defaults ?? {}), ...Object.keys(draft.cost_multipliers)])].sort()
  );

  function override(prefix: string, raw: string) {
    const next = { ...draft.cost_multipliers };
    if (raw.trim() === '') delete next[prefix];
    else {
      const value = Number(raw);
      if (!Number.isFinite(value)) return;
      next[prefix] = value;
    }
    draft.cost_multipliers = next;
    onchange();
  }

  /* ---------------------------------------------------------------------- */
  /* the per-axis detail the Rankings screen used to be                      */
  /* ---------------------------------------------------------------------- */

  const ranked = $derived((ranking?.ranks ?? []).filter((rank) => rank.position > 0));
  const setAside = $derived((ranking?.ranks ?? []).filter((rank) => rank.position === 0));
  const weightValues = $derived(
    Object.fromEntries(Object.entries(draft.weights).map(([axis, w]) => [axis, w.value]))
  );

  /* ---------------------------------------------------------------------- */
  /* rename and delete                                                       */
  /* ---------------------------------------------------------------------- */

  async function rename() {
    const next = renaming.trim();
    if (!next || next === profile.name) return;
    busy = 'rename';
    const result = await api.renameProfile(profile.name, next, options);
    busy = '';
    if (!result.ok) {
      onfailed(explainError(result.error));
      return;
    }
    onchanged(`${profile.name} is now ${next}.`);
  }

  /**
   * Delete, and the second ask.
   *
   * The server refuses while a write connector may still be holding this
   * profile's combo. That refusal is worth showing rather than routing around,
   * so the forced delete is a second button that only appears once the first
   * has said why.
   */
  let forcing = $state(false);

  async function remove(force = false) {
    busy = 'delete';
    const result = await api.removeProfile(profile.name, force, options);
    busy = '';
    confirming = false;
    if (!result.ok) {
      if (result.error.code === 'in_use') {
        forcing = true;
        onfailed(`${result.error.message} — delete anyway?`);
        return;
      }
      onfailed(explainError(result.error));
      return;
    }
    forcing = false;
    onchanged(`${profile.name} deleted.`);
  }

  const when = (value: string) =>
    value ? new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '';

  /** `before`/`after` are whatever the server recorded; show them, don't parse them. */
  function show(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
</script>

<div class="detail">
  <!-- ---- every axis ---------------------------------------------------- -->
  <section class="panel wide">
    <h3>Every axis</h3>
    <p class="hint">
      The value is what the seat cares about; min and max are the room it is allowed to move in,
      and a locked axis holds while the others absorb a change.
      {#if settingsAbsent}
        This server has no settings route yet, so the bounds are open and are not saved.
      {/if}
    </p>
    {#each axes as [axis, control] (axis)}
      <div class="axis">
        <WeightSlider
          {axis}
          value={control.value}
          min={control.min}
          max={control.max}
          locked={control.locked}
          idPrefix={`d-${profile.name}`}
          onchange={(value) => setValue(axis, value)}
          onlock={(next) => {
            control.locked = next;
            onchange();
          }}
        />
        <label class="bound">
          <span>min</span>
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            disabled={settingsAbsent}
            value={control.min}
            onchange={(e) => bound(axis, 'min', Number(e.currentTarget.value))}
          />
        </label>
        <label class="bound">
          <span>max</span>
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            disabled={settingsAbsent}
            value={control.max}
            onchange={(e) => bound(axis, 'max', Number(e.currentTarget.value))}
          />
        </label>
      </div>
    {/each}

    <label class="floor">
      <span>Floor score <small>below this a model is set aside, not ranked low</small></span>
      <input
        type="number"
        min="0"
        max="1"
        step="0.05"
        value={draft.floor_score}
        onchange={(e) => {
          draft.floor_score = Number(e.currentTarget.value);
          onchange();
        }}
      />
    </label>
  </section>

  <!-- ---- cost multipliers ---------------------------------------------- -->
  <section class="panel">
    <h3>Cost multipliers</h3>
    <p class="hint">
      What a local id really costs you, against its published price. Blank uses the default, which
      is set once on the Connectors screen and shown here in grey.
    </p>
    {#if prefixes.length === 0}
      <p class="hint muted">
        {defaults === null
          ? 'This server has no cost-multiplier route yet.'
          : 'No prefixes configured yet.'}
      </p>
    {:else}
      {#each prefixes as prefix (prefix)}
        <label class="multiplier">
          <span class="mono">{prefix}</span>
          <input
            type="number"
            min="0"
            step="0.05"
            disabled={settingsAbsent}
            placeholder={String(defaults?.[prefix] ?? 1)}
            value={draft.cost_multipliers[prefix] ?? ''}
            onchange={(e) => override(prefix, e.currentTarget.value)}
          />
          <small class="default">default {defaults?.[prefix] ?? 1}</small>
        </label>
      {/each}
    {/if}
  </section>

  <!-- ---- name and life ------------------------------------------------- -->
  <section class="panel">
    <h3>Name and life</h3>
    <label class="multiplier">
      <span>Name</span>
      <input bind:value={renaming} pattern="[A-Za-z0-9_\-]+" autocomplete="off" />
    </label>
    <div class="acts">
      <button
        type="button"
        onclick={() => void rename()}
        disabled={busy === 'rename' || renaming.trim() === profile.name || !renaming.trim()}
      >
        {busy === 'rename' ? 'Renaming…' : 'Rename'}
      </button>
      {#if confirming}
        <span class="confirm">
          Delete {profile.name}?
          <button type="button" class="danger" onclick={() => void remove()} disabled={busy === 'delete'}>
            {busy === 'delete' ? 'Deleting…' : 'Yes, delete'}
          </button>
          <button type="button" onclick={() => (confirming = false)}>Keep</button>
        </span>
      {:else if forcing}
        <span class="confirm">
          <button type="button" class="danger" onclick={() => void remove(true)} disabled={busy === 'delete'}>
            {busy === 'delete' ? 'Deleting…' : 'Delete anyway'}
          </button>
          <button type="button" onclick={() => (forcing = false)}>Keep it</button>
        </span>
      {:else}
        <button type="button" onclick={() => (confirming = true)}>Delete</button>
      {/if}
    </div>
    <p class="hint">
      Deleting a profile takes its seat off every gateway the next time Sieve writes. It cannot be
      undone from here.
    </p>
  </section>

  <!-- ---- constraints, shape and policy ---------------------------------- -->
  <section class="panel wide">
    <h3>What it refuses, what a task costs, when it changes its mind</h3>
    <p class="hint">
      These three save on their own, at once, because they are three decisions and one Apply would
      make them look like one. Chain length and "ship on its own" are the same two numbers as List
      length and auto-apply on the row above.
    </p>
    <SettingsBlocks
      {profile}
      {token}
      onsaved={(_updated, what) => onsaved(`Saved ${what}.`)}
      onerror={(failure) => onfailed(explainError(failure))}
    />
  </section>

  <!-- ---- the per-axis detail ------------------------------------------- -->
  <section class="panel wide">
    <h3>What carried each score</h3>
    {#if ranked.length === 0}
      <p class="hint muted">Nothing ranks for this profile yet.</p>
    {:else}
      <div class="scroll-x">
        <table>
          <thead>
            <tr>
              <th class="pos">#</th>
              <th>model</th>
              <th>axes</th>
              <th class="right">score</th>
              <th>conf</th>
              <th class="right">cost</th>
              <th>reach</th>
            </tr>
          </thead>
          <tbody>
            {#each ranked as rank (rank.model_id)}
              <tr class:lead={rank.position === 1}>
                <td class="pos num">{rank.position}</td>
                <td>
                  <div class="id mono">{rank.model_id}</div>
                  {#if rank.local_ids?.length}
                    <div class="local mono">{rank.local_ids.join(' · ')}</div>
                  {/if}
                  {#if rank.position === 1 && rank.flip}
                    <div class="flip">{rank.flip}</div>
                  {/if}
                </td>
                <td><AxisBars axes={rank.axes ?? []} weights={weightValues} /></td>
                <td class="right num">{rank.final.toFixed(3)}</td>
                <td><ConfDots confidence={rank.confidence} /></td>
                <td class="right num">
                  {rank.cost_per_task == null ? '—' : `$${rank.cost_per_task.toPrecision(3)}`}
                </td>
                <td>
                  <span class="dot" class:on={rank.reachable}></span>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if setAside.length}
        <ul class="aside">
          {#each setAside as rank (rank.model_id)}
            <li>
              <span class="mono">{rank.model_id}</span>
              <span class="reason">
                {rank.dominated_by
                  ? `dominated by ${rank.dominated_by}`
                  : `excluded: ${rank.excluded_by}`}
              </span>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </section>

  <!-- ---- history ------------------------------------------------------- -->
  <section class="panel">
    <h3>History</h3>
    {#if historyNote}<p class="hint muted">{historyNote}</p>{/if}
    <ul class="log">
      {#each history.slice(0, 20) as entry, index (`${entry.when}-${index}`)}
        <li>
          <span class="who">{entry.who}</span>
          <span class="what">{entry.what}</span>
          <span class="at">{when(entry.when)}</span>
          {#if entry.before !== undefined || entry.after !== undefined}
            <span class="mono change" title={`${show(entry.before)} → ${show(entry.after)}`}>
              {show(entry.before)} → {show(entry.after)}
            </span>
          {/if}
        </li>
      {:else}
        <li class="muted">Nothing recorded.</li>
      {/each}
    </ul>
  </section>

  <!-- ---- experience ---------------------------------------------------- -->
  <section class="panel">
    <h3>Experience</h3>
    <p class="hint">How the models on this seat have actually behaved when your agents called them.</p>
    {#if experienceNote}<p class="hint muted">{experienceNote}</p>{/if}
    <ul class="log">
      {#each experience as row (row.model_id)}
        <li>
          <span class="mono">{row.model_id}</span>
          <span class="num rate" title="smoothed: (successes + 1) / (calls + 2)">
            {(row.experience * 100).toFixed(0)}%
          </span>
          <span class="at">{row.successes}/{row.outcomes} ok</span>
        </li>
      {/each}
    </ul>
  </section>
</div>

<style>
  .detail {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr));
    gap: 0.7rem;
    margin-top: 0.9rem;
    padding-top: 0.9rem;
    border-top: 1px solid var(--rule);
  }
  .panel {
    border: 1px solid var(--rule);
    border-radius: 8px;
    background: var(--panel2);
    padding: 0.7rem 0.8rem 0.8rem;
    min-width: 0;
  }
  .panel.wide {
    grid-column: 1 / -1;
  }
  h3 {
    margin: 0 0 0.25rem;
    font-size: 0.86rem;
    font-family: var(--ui);
  }
  .hint {
    margin: 0 0 0.6rem;
    color: var(--muted);
    font-size: 0.73rem;
    line-height: 1.45;
  }
  .axis {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 5rem 5rem;
    gap: 0.5rem;
    align-items: center;
  }
  .bound,
  .floor,
  .multiplier {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.72rem;
    color: var(--muted);
    min-width: 0;
  }
  .bound span {
    width: 1.8rem;
  }
  .floor {
    margin-top: 0.7rem;
    justify-content: space-between;
  }
  .floor small {
    display: block;
    font-size: 0.68rem;
  }
  .multiplier {
    justify-content: space-between;
    padding: 0.15rem 0;
  }
  .multiplier .default {
    color: var(--muted);
    opacity: 0.7;
    font-size: 0.68rem;
    white-space: nowrap;
  }
  input {
    background: var(--panel);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font: inherit;
    font-size: 0.76rem;
    padding: 0.18rem 0.35rem;
    min-width: 0;
    width: 4.6rem;
  }
  .multiplier input:not([type]) {
    width: 9rem;
  }
  input:disabled {
    opacity: 0.55;
  }
  .acts {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    flex-wrap: wrap;
    margin: 0.5rem 0;
  }
  .confirm {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.72rem;
    color: var(--muted);
  }
  button {
    background: var(--panel);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--muted);
    font: inherit;
    font-size: 0.72rem;
    padding: 0.15rem 0.55rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    color: var(--ink);
    border-color: var(--accent);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button.danger:hover:not(:disabled) {
    color: var(--bad);
    border-color: var(--bad);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    min-width: 40rem;
  }
  thead th {
    text-align: left;
    font-weight: 500;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    padding: 0.3rem 0.4rem;
    border-bottom: 1px solid var(--rule);
  }
  td {
    padding: 0.35rem 0.4rem;
    border-bottom: 1px solid var(--rule);
    vertical-align: middle;
  }
  tr.lead td {
    background: color-mix(in oklab, var(--accent) 7%, transparent);
  }
  tr.lead .id {
    color: var(--accent);
  }
  .pos {
    width: 2rem;
    color: var(--muted);
  }
  .right {
    text-align: right;
  }
  .id {
    overflow-wrap: anywhere;
  }
  .local,
  .flip {
    color: var(--muted);
    font-size: 0.68rem;
  }
  .flip {
    color: var(--accent);
    font-family: var(--ui);
  }
  .dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--rule);
  }
  .dot.on {
    background: var(--reach);
  }
  .aside,
  .log {
    list-style: none;
    margin: 0.5rem 0 0;
    padding: 0;
    color: var(--muted);
    font-size: 0.74rem;
  }
  .aside li,
  .log li {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    justify-content: space-between;
    padding: 0.22rem 0;
    border-bottom: 1px solid var(--rule);
  }
  .who {
    color: var(--ink);
  }
  .what {
    flex: 1 1 12rem;
    overflow-wrap: anywhere;
  }
  .at {
    white-space: nowrap;
  }
  .rate {
    color: var(--good);
  }
  .change {
    flex: 1 1 100%;
    font-size: 0.68rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .muted {
    color: var(--muted);
  }
  @media (max-width: 700px) {
    .axis {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
