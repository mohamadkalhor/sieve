<script lang="ts">
  /**
   * Everything about a profile that is not a weight: what it refuses, what a
   * task on it looks like, and how willing it is to change its mind.
   *
   * Phase 1 shipped these read-only — the routes existed and no screen called
   * them — so a seat could only ever be tuned by editing YAML on the server.
   * Each block saves on its own, because they are three different decisions and
   * one Save button would make them look like one.
   */
  import { api, type ApiError } from '$lib/api/client';
  import type { Profile } from '$lib/types';

  interface Props {
    profile: Profile;
    token: string;
    onsaved: (updated: Profile, what: string) => void;
    onerror: (error: ApiError) => void;
  }
  let { profile, token, onsaved, onerror }: Props = $props();

  /** Every constraint the server understands; anything else it rejects by name. */
  const CONSTRAINTS = [
    { key: 'tools', label: 'Tool calling', hint: 'the model can be given functions' },
    { key: 'reasoning', label: 'Reasoning', hint: 'a thinking mode is available' },
    { key: 'structured_output', label: 'Structured output', hint: 'JSON schema honoured' }
  ] as const;

  const SHAPE = [
    { key: 'in_tokens', label: 'Input tokens', step: 100 },
    { key: 'out_tokens', label: 'Output tokens', step: 100 },
    { key: 'images', label: 'Images', step: 1 },
    { key: 'seconds', label: 'Seconds', step: 1 },
    { key: 'chars', label: 'Characters', step: 100 }
  ] as const;

  let busy = $state('');

  // Local copies, so a half-typed number never re-ranks anything behind you.
  // Filled by the effect below rather than inline, so switching profile
  // replaces them instead of leaving the first one's values in the boxes.
  type PolicyOf = NonNullable<Profile['policy']>;
  let policy = $state<PolicyOf>({} as PolicyOf);
  let require = $state<Record<string, unknown>>({});
  let shape = $state<Record<string, number | null>>({});

  $effect(() => {
    const loaded = profile;
    policy = { ...loaded.policy } as PolicyOf;
    require = { ...(loaded.require ?? {}) };
    shape = { ...(loaded.shape as Record<string, number | null>) };
  });

  async function save(what: 'policy' | 'constraints' | 'shape') {
    busy = what;
    const options = { token: token || undefined };
    const result =
      what === 'policy'
        ? await api.setPolicy(profile.name, policy as unknown as Record<string, unknown>, options)
        : what === 'constraints'
          ? await api.setConstraints(profile.name, require, options)
          : await api.setShape(
              profile.name,
              Object.fromEntries(
                Object.entries(shape).filter(([, v]) => v !== null && v !== undefined)
              ),
              options
            );
    busy = '';
    if (result.ok) onsaved(result.value, what);
    else onerror(result.error);
  }

  function toggle(key: string, on: boolean) {
    const next = { ...require };
    // Removed, not set to false: `{tools: false}` reads as "require the
    // absence of tools", which is not a thing anybody wants.
    if (on) next[key] = true;
    else delete next[key];
    require = next;
  }

  const contextMin = $derived(Number(require.context_min ?? 0));
</script>

<section class="settings">
  <!-- ---------------------------------------------------------------- -->
  <div class="block">
    <h3>What it refuses</h3>
    <p class="hint">
      A model that fails one of these is set aside with the reason, not ranked low.
    </p>

    {#each CONSTRAINTS as c (c.key)}
      <label class="row check">
        <input
          type="checkbox"
          checked={require[c.key] === true}
          onchange={(e) => toggle(c.key, e.currentTarget.checked)}
        />
        <span>
          {c.label}
          <small>{c.hint}</small>
        </span>
      </label>
    {/each}

    <label class="row">
      <span>Minimum context</span>
      <input
        type="number"
        min="0"
        step="1000"
        value={contextMin}
        onchange={(e) => {
          const v = Number(e.currentTarget.value);
          const next = { ...require };
          if (v > 0) next.context_min = v;
          else delete next.context_min;
          require = next;
        }}
      />
    </label>

    <button type="button" onclick={() => save('constraints')} disabled={busy === 'constraints'}>
      {busy === 'constraints' ? 'Saving…' : 'Save constraints'}
    </button>
  </div>

  <!-- ---------------------------------------------------------------- -->
  <div class="block">
    <h3>What a task looks like</h3>
    <p class="hint">
      Cost is the published price times this. A reader paying for 200k input and a chat paying
      for 2k do not share a cost ranking.
    </p>

    {#each SHAPE as f (f.key)}
      <label class="row">
        <span>{f.label}</span>
        <input
          type="number"
          min="0"
          step={f.step}
          value={shape[f.key] ?? ''}
          placeholder="—"
          onchange={(e) => {
            const raw = e.currentTarget.value;
            shape = { ...shape, [f.key]: raw === '' ? null : Number(raw) };
          }}
        />
      </label>
    {/each}

    <button type="button" onclick={() => save('shape')} disabled={busy === 'shape'}>
      {busy === 'shape' ? 'Saving…' : 'Save shape'}
    </button>
  </div>

  <!-- ---------------------------------------------------------------- -->
  <div class="block">
    <h3>How willing it is to change its mind</h3>
    <p class="hint">
      The margin is what stops a seat flapping between two models a fraction apart.
    </p>

    <label class="row">
      <span>Margin <small>points a challenger needs</small></span>
      <input type="number" min="0" step="0.5" bind:value={policy.margin} />
    </label>
    <label class="row">
      <span>Chain length <small>primary plus fallbacks</small></span>
      <input type="number" min="1" step="1" bind:value={policy.chain} />
    </label>
    <label class="row">
      <span>Max tenure <small>days before it re-contests</small></span>
      <input type="number" min="0" step="1" bind:value={policy.max_tenure_days} />
    </label>
    <label class="row">
      <span>Min confidence <small>below this, set aside</small></span>
      <input type="number" min="0" max="1" step="0.05" bind:value={policy.min_confidence} />
    </label>
    <label class="row">
      <span>Suspend below health</span>
      <input type="number" min="0" max="1" step="0.05" bind:value={policy.suspend_below_health} />
    </label>

    <label class="row check">
      <input type="checkbox" bind:checked={policy.auto_apply} />
      <span>
        Ship on its own
        <small>the scheduled run writes this seat to your targets without asking</small>
      </span>
    </label>
    <label class="row check">
      <input type="checkbox" bind:checked={policy.require_telemetry} />
      <span>
        Only models I have called
        <small>a benchmark says it is good; your own traffic says it works</small>
      </span>
    </label>

    {#if policy.auto_apply}
      <p class="warn">
        This seat will change what your gateway routes to, unattended, whenever the ranking
        moves by more than the margin.
      </p>
    {/if}

    <button type="button" onclick={() => save('policy')} disabled={busy === 'policy'}>
      {busy === 'policy' ? 'Saving…' : 'Save policy'}
    </button>
  </div>
</section>

<style>
  .settings {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    margin-top: 1rem;
  }
  .block {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.85rem 0.9rem 1rem;
    min-width: 0;
  }
  h3 {
    margin: 0 0 0.2rem;
    font-size: 0.9rem;
  }
  .hint {
    margin: 0 0 0.7rem;
    color: var(--muted);
    font-size: 0.76rem;
    line-height: 1.45;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.3rem 0;
    font-size: 0.82rem;
  }
  .row.check {
    justify-content: flex-start;
    align-items: flex-start;
  }
  .row small {
    display: block;
    color: var(--muted);
    font-size: 0.72rem;
    line-height: 1.35;
  }
  input[type='number'] {
    width: 6.5rem;
    padding: 0.25rem 0.4rem;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--bg);
    color: inherit;
    font: inherit;
    font-size: 0.82rem;
    text-align: right;
  }
  input[type='checkbox'] {
    margin-top: 0.2rem;
  }
  .warn {
    margin: 0.5rem 0 0;
    font-size: 0.75rem;
    line-height: 1.45;
    color: #b7791f;
  }
  button {
    margin-top: 0.7rem;
    width: 100%;
    padding: 0.35rem 0.6rem;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel2);
    color: inherit;
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.6;
    cursor: default;
  }
</style>
