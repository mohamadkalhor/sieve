<script lang="ts">
  /**
   * One profile, as a row: what it is, the controls that shape it, and the
   * list those controls produce -- side by side, so a weight and its
   * consequence are never on two different screens.
   *
   * WHAT IT LOADS, AND WHEN
   *
   * A row costs a chain and its settings to show: about a kilobyte. A ranking
   * on this box is 765 KB, and there are twenty-two profiles -- so fetching one
   * per row to fill the right-hand column would be seventeen megabytes before
   * anybody had touched anything, most of it for seats they were not going to
   * look at. Until a row is engaged its right column shows what that seat is
   * shipping now, dim, which is a true answer and free.
   *
   * Touching a control, or opening the card, engages it: one `preview` comes
   * back with the list *and* the ranking behind it, so from then on the row
   * re-ranks in the browser on every input with the same arithmetic the server
   * uses (`$lib/rank/weigh`, a port asserted against the Python to 1e-6). The
   * list tracks the finger, and 250 ms after the last input the server's own
   * answer replaces it.
   *
   * WHAT HAPPENS ON A SERVER THAT IS HALF WAY THROUGH THIS FEATURE
   *
   * Every new route degrades to something that already exists rather than to
   * an error: the settings come from the profile's own weights and policy, the
   * preview stays local, Apply writes through the weights and policy routes
   * phase 1 shipped, and the history is read from `/v1/decisions`. Only
   * pinning, removing, price sensitivity and experience weight have no older
   * equivalent, and those are disabled with a sentence saying why rather than
   * offered and then failing.
   */
  import {
    api,
    explainError,
    type ApiError,
    type ModelStatus,
    type PreviewResult,
    type ProfileSettings,
    type Result,
    type WeightControl
  } from '$lib/api/client';
  import type { Chain, Profile, Ranking } from '$lib/types';
  import ProfileDetail from '$lib/components/ProfileDetail.svelte';
  import WeightSlider from '$lib/components/WeightSlider.svelte';
  import { ago } from '$lib/freshness';
  import { duration, reducedMotion } from '$lib/motion/reduced';
  import { rankWithFloor, renormalise, weigh, type AxesByModel } from '$lib/rank/weigh';
  import { flip } from 'svelte/animate';

  interface Props {
    profile: Profile;
    /** a token with `profiles:write` and `apply`, shared by the whole screen */
    token: string;
    open: boolean;
    /** the defaults from `/v1/cost-multipliers`, or null where it is absent */
    defaults: Record<string, number> | null;
    /** ticks once a minute, so "shipped 51 min ago" keeps counting */
    now: Date;
    ontoggle: () => void;
    /** the profile was renamed or deleted: the screen has to reload its list */
    onchanged: (message: string) => void;
  }
  let { profile, token, open, defaults, now, ontoggle, onchanged }: Props = $props();

  /** how many weights a row shows before it asks to be expanded */
  const SHOWN_WEIGHTS = 5;

  let chain = $state<Chain | null>(null);
  let ranking = $state<Ranking | null>(null);
  let settings = $state<ProfileSettings | null>(null);
  let draft = $state<ProfileSettings | null>(null);
  let loading = $state(true);

  /** the settings route is not on this server yet */
  let settingsAbsent = $state(false);
  /** the preview route is not on this server yet: the local rank stands */
  let previewAbsent = $state(false);
  /** the status route is not on this server yet: pin and remove are off */
  let statusAbsent = $state(false);

  /** this row has asked the server for its list at least once */
  let engaged = $state(false);
  let preview = $state<PreviewResult | null>(null);
  let previewing = $state(false);
  /**
   * Pins and removals this session made. The server holds the truth and proves
   * it by where the ids come back in `preview.models`; there is no bulk read of
   * the statuses, so the button label is only ever what this page did.
   */
  let statuses = $state<Record<string, ModelStatus>>({});
  let allWeights = $state(false);
  let busy = $state('');
  let said = $state<{ ok: boolean; text: string } | null>(null);

  const options = $derived({ token: token || undefined });

  /* ---------------------------------------------------------------------- */
  /* loading                                                                 */
  /* ---------------------------------------------------------------------- */

  /**
   * A route that is not mounted does not always answer 404. FastAPI serves the
   * built web app at `/`, so a path it does not recognise comes back as the SPA
   * shell -- 200, text/html, which the client hands over as a null body. Both
   * shapes mean the same thing, and both have been measured on this server.
   */
  function absent<T>(result: Result<T>): boolean {
    if (!result.ok) return result.error.status === 404 || result.error.code === 'not_built';
    return result.value === null || result.value === undefined;
  }

  const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

  /** The controls a profile implies, for a server without the settings route. */
  function fromProfile(p: Profile): ProfileSettings {
    const weights: Record<string, WeightControl> = {};
    for (const [axis, value] of Object.entries(p.weights)) {
      weights[axis] = { value, min: 0, max: 1, locked: false };
    }
    return {
      list_length: p.policy?.chain ?? 5,
      floor_score: p.policy?.min_confidence ?? 0,
      // Not guessed at. There is no older field that means either of these, so
      // they read zero and their controls are disabled until the route lands.
      price_sensitivity: 1,
      experience_weight: 0,
      auto_apply: p.policy?.auto_apply ?? false,
      weights,
      cost_multipliers: {}
    };
  }

  $effect(() => {
    const wanted = profile.name;
    loading = true;
    void (async () => {
      const [c, s] = await Promise.all([api.chain(wanted), api.profileSettings(wanted)]);
      if (wanted !== profile.name) return;

      chain = c.ok ? c.value : null;

      if (absent(s) || !s.ok || !s.value.weights) {
        settings = fromProfile(profile);
        settingsAbsent = true;
      } else {
        settings = s.value;
        settingsAbsent = false;
      }
      draft = clone(settings);
      axesOrder = Object.entries(settings.weights)
        .sort(([aAxis, a], [bAxis, b]) => b.value - a.value || aAxis.localeCompare(bAxis))
        .map(([axis]) => axis);
      loading = false;
    })();
  });

  /** opening the card is engagement: the detail wants the ranking too */
  $effect(() => {
    if (open && !loading) void engage();
  });

  /* ---------------------------------------------------------------------- */
  /* the list                                                                */
  /* ---------------------------------------------------------------------- */

  interface Row {
    rank: number;
    id: string;
    local_ids: string[];
    score: number;
    status: ModelStatus;
  }

  /** axis values per model, straight from the ranking rows the server sent */
  const axesByModel = $derived.by(() => {
    const out: AxesByModel = {};
    for (const rank of ranking?.ranks ?? []) {
      if (rank.excluded_by === 'min_confidence' || rank.dominated_by) continue;
      out[rank.model_id] = Object.fromEntries(
        (rank.axes ?? []).map((axis) => [axis.axis, { value: axis.value, coverage: axis.coverage }])
      );
    }
    return out;
  });

  const localIds = $derived.by(() => {
    const out: Record<string, string[]> = {};
    for (const rank of ranking?.ranks ?? []) out[rank.model_id] = rank.local_ids ?? [];
    return out;
  });

  const weightValues = $derived.by(() => {
    const out: Record<string, number> = {};
    for (const [axis, control] of Object.entries(draft?.weights ?? {})) out[axis] = control.value;
    return out;
  });

  /** Pinned first, removed gone, positions renumbered from what is left. */
  function arrange(rows: Row[]): Row[] {
    const kept = rows.filter((row) => row.status !== 'removed');
    const pinned = kept.filter((row) => row.status === 'pinned');
    const rest = kept.filter((row) => row.status !== 'pinned');
    return [...pinned, ...rest].map((row, index) => ({ ...row, rank: index + 1 }));
  }

  const localRows = $derived.by(() =>
    rankWithFloor(weigh(axesByModel, weightValues), draft?.floor_score ?? 0).map((row, index) => ({
      rank: index + 1,
      id: row.model_id,
      local_ids: localIds[row.model_id] ?? [],
      score: row.score,
      status: statuses[row.model_id] ?? ('active' as ModelStatus)
    }))
  );

  /**
   * The server's answer, turned into rows.
   *
   * `models` is already the whole decision -- pinned first, then everything
   * active and reachable above the floor, cut to the list length -- so it is
   * not re-sorted or re-cut here. The ranking beside it only supplies the
   * score and the local ids to print against each id.
   */
  const previewRows = $derived.by(() => {
    if (!preview) return null;
    const scored = new Map((preview.ranking?.ranks ?? []).map((rank) => [rank.model_id, rank]));
    return preview.models.map((id, index) => ({
      rank: index + 1,
      id,
      local_ids: scored.get(id)?.local_ids ?? localIds[id] ?? [],
      score: scored.get(id)?.final ?? 0,
      status: statuses[id] ?? ('active' as ModelStatus)
    }));
  });

  const shippedIds = $derived.by(() => {
    if (!chain) return [] as string[];
    return [chain.primary, ...(chain.fallbacks ?? [])].filter((id): id is string => !!id);
  });
  const shippedAt = $derived.by(() => {
    const at = new Map<string, number>();
    shippedIds.forEach((id, index) => at.set(id, index + 1));
    return at;
  });

  const cut = $derived(Math.max(1, draft?.list_length ?? 5));

  /** what the gateway is serving now, as rows: the answer before any request */
  const shippedRows = $derived(
    shippedIds.map((id, index) => ({
      rank: index + 1,
      id,
      local_ids: chain?.local?.[id] ?? [],
      score: 0,
      status: 'active' as ModelStatus
    }))
  );

  /** the bright list: what this draft would ship */
  const shipping = $derived(
    previewRows ?? (engaged ? arrange(localRows).slice(0, cut) : shippedRows)
  );

  /** what is on the gateway now and would fall off this draft: dim, behind */
  const dropping = $derived(
    shippedIds
      .filter((id) => !shipping.some((row) => row.id === id))
      .map((id, index) => ({ id, was: shippedAt.get(id) ?? index + 1 }))
  );

  /* ---------------------------------------------------------------------- */
  /* the state chip                                                          */
  /* ---------------------------------------------------------------------- */

  const dirty = $derived(
    draft !== null && settings !== null && JSON.stringify(draft) !== JSON.stringify(settings)
  );

  const chip = $derived.by(() => {
    if (loading) return { text: 'loading…', tone: 'muted' };
    if (dirty) return { text: 'changed, not applied', tone: 'accent' };
    if (!chain) return { text: 'no chain yet', tone: 'muted' };
    const when = chain.computed_at ? ago(new Date(chain.computed_at), now) : 'at some point';
    return { text: `shipped ${when}${draft?.auto_apply ? ' · auto' : ''}`, tone: 'good' };
  });

  /* ---------------------------------------------------------------------- */
  /* the controls                                                            */
  /* ---------------------------------------------------------------------- */

  /**
   * The order the sliders sit in, fixed when the row loads.
   *
   * Sorting by the live value instead would re-sort the sliders under the
   * finger the moment one of them moved -- which drops the drag, and puts a
   * different axis where the one you were holding used to be.
   */
  let axesOrder = $state<string[]>([]);

  const axesSorted = $derived(
    axesOrder
      .map((axis) => [axis, draft?.weights[axis]] as const)
      .filter((pair): pair is readonly [string, WeightControl] => pair[1] !== undefined)
  );
  const axesShown = $derived(
    axesSorted.length <= SHOWN_WEIGHTS || allWeights ? axesSorted : axesSorted.slice(0, SHOWN_WEIGHTS)
  );
  const moreWeights = $derived(axesSorted.length - axesShown.length);

  function move(axis: string, value: number) {
    if (!draft) return;
    const locked = new Set(
      Object.entries(draft.weights)
        .filter(([, control]) => control.locked)
        .map(([name]) => name)
    );
    const next = renormalise(weightValues, axis, value, locked);
    for (const [name, weight] of Object.entries(next)) {
      const control = draft.weights[name];
      if (control) control.value = weight;
    }
    touched();
  }

  function toggleLock(axis: string, next: boolean) {
    const control = draft?.weights[axis];
    if (control) control.locked = next;
  }

  /* ---------------------------------------------------------------------- */
  /* preview                                                                 */
  /* ---------------------------------------------------------------------- */

  let timer: ReturnType<typeof setTimeout> | null = null;
  /** the newest request wins, whatever order the answers come back in */
  let latest = 0;

  /**
   * Fetch this row's list, once, the first time somebody shows interest.
   *
   * One `preview` answers with the list and the ranking behind it, so there is
   * no second request for the axis values the local re-rank needs. A server
   * without that route falls back to the ranking on its own, which is the same
   * payload by a longer road.
   */
  async function engage() {
    if (engaged || !draft) return;
    engaged = true;
    await runPreview();
    if (previewAbsent && !ranking) {
      const result = await api.ranking(profile.name);
      if (result.ok) ranking = result.value;
    }
  }

  /** Something moved: the local list is already right, ask the server too. */
  function touched() {
    said = null;
    if (!engaged) void engage();
    else schedulePreview();
  }

  function schedulePreview() {
    if (previewAbsent || !draft) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void runPreview(), 250);
  }

  async function runPreview() {
    if (!draft || previewAbsent) return;
    const mine = ++latest;
    previewing = true;
    const result = await api.preview(profile.name, clone(draft), options);
    if (mine !== latest) return;
    previewing = false;

    if (absent(result)) {
      previewAbsent = true;
      preview = null;
      return;
    }
    if (!result.ok) {
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    preview = result.value;
    // the ranking behind the list, which is what makes the next input instant
    if (result.value.ranking) ranking = result.value.ranking;
  }

  $effect(() => () => {
    if (timer) clearTimeout(timer);
  });

  /* ---------------------------------------------------------------------- */
  /* writing                                                                 */
  /* ---------------------------------------------------------------------- */

  async function setStatus(id: string, status: ModelStatus) {
    const before = statuses[id] ?? 'active';
    statuses = { ...statuses, [id]: status };
    busy = `status:${id}`;
    const result = await api.setModelStatus(profile.name, id, status, options);
    busy = '';

    if (absent(result)) {
      statuses = { ...statuses, [id]: before };
      statusAbsent = true;
      said = {
        ok: false,
        text: 'Pinning and removing need the per-model status route, which this server does not have yet.'
      };
      return;
    }
    if (!result.ok) {
      statuses = { ...statuses, [id]: before };
      said = { ok: false, text: explainError(result.error) };
      return;
    }
    preview = null;
    schedulePreview();
  }

  /**
   * Apply: write the draft down, then ship it.
   *
   * Where the settings route is absent the same draft goes to the two routes
   * phase 1 shipped -- weights and policy -- which between them hold every
   * field this screen can actually change on such a server. That is why Apply
   * is offered rather than greyed out before the new module lands.
   */
  async function save(): Promise<ApiError | null> {
    if (!draft) return null;
    if (!settingsAbsent) {
      const result = await api.saveProfileSettings(profile.name, clone(draft), options);
      if (!absent(result)) {
        if (!result.ok) return result.error;
        settings = result.value;
        draft = clone(result.value);
        return null;
      }
      settingsAbsent = true;
    }

    const weights = await api.setWeights(profile.name, clone(weightValues), options);
    if (!weights.ok) return weights.error;
    const policy = await api.setPolicy(
      profile.name,
      {
        chain: draft.list_length,
        min_confidence: draft.floor_score,
        auto_apply: draft.auto_apply
      },
      options
    );
    if (!policy.ok) return policy.error;
    settings = clone(draft);
    return null;
  }

  async function apply() {
    busy = 'apply';
    said = null;

    const failed = await save();
    if (failed) {
      busy = '';
      said = { ok: false, text: explainError(failed) };
      return;
    }

    let where: string[] = [];
    const result = await api.applyProfile(profile.name, options);
    if (absent(result)) {
      // `/v1/apply` has taken a list of profiles since phase 1.
      const legacy = await api.apply([profile.name], options);
      busy = '';
      if (!legacy.ok) {
        said = { ok: false, text: explainError(legacy.error) };
        return;
      }
      where = (legacy.value ?? []).map((target) => target.target);
    } else {
      busy = '';
      if (!result.ok) {
        said = { ok: false, text: explainError(result.error) };
        return;
      }
      where = (result.value.results ?? []).map((target) => target.target);
      if (result.value.chain) chain = result.value.chain;
    }

    said = { ok: true, text: where.length ? `Shipped to ${where.join(', ')}.` : 'Shipped.' };
    const again = await api.chain(profile.name);
    if (again.ok) chain = again.value;
  }

  function discard() {
    if (!settings) return;
    draft = clone(settings);
    preview = null;
    said = null;
    schedulePreview();
  }

  async function toggleAuto(on: boolean) {
    if (!draft) return;
    draft.auto_apply = on;
    busy = 'auto';
    const failed = await save();
    busy = '';
    if (failed) said = { ok: false, text: explainError(failed) };
  }
</script>

<li class="row" class:open>
  <div class="three">
    <!-- LEFT: what this seat is ------------------------------------------ -->
    <div class="card">
      <button
        type="button"
        class="cardhit"
        aria-expanded={open}
        onclick={ontoggle}
        title={open ? 'close the deeper settings' : 'open the deeper settings'}
      >
        <span class="name">{profile.name}</span>
        <span class="purpose">{profile.purpose}</span>
        <span class="modality">{profile.modality}</span>
      </button>
      <span class="chip" data-tone={chip.tone}>{chip.text}</span>
    </div>

    <!-- MIDDLE: the controls --------------------------------------------- -->
    <div class="controls">
      {#if loading}
        <p class="muted small">Loading…</p>
      {:else}
        {#each axesShown as [axis, control] (axis)}
          <WeightSlider
            {axis}
            value={control.value}
            min={control.min}
            max={control.max}
            locked={control.locked}
            idPrefix={`w-${profile.name}`}
            onchange={(value) => move(axis, value)}
            onlock={(next) => toggleLock(axis, next)}
          />
        {/each}
        {#if moreWeights > 0}
          <button type="button" class="more" onclick={() => (allWeights = true)}>
            +{moreWeights} more
          </button>
        {:else if allWeights && axesSorted.length > SHOWN_WEIGHTS}
          <button type="button" class="more" onclick={() => (allWeights = false)}>
            show the largest {SHOWN_WEIGHTS}
          </button>
        {/if}

        <p class="sum mono">
          sum {Object.values(weightValues)
            .reduce((a, b) => a + b, 0)
            .toFixed(3)}
        </p>

        <div class="numbers">
          <label>
            <span>List length</span>
            <input
              id={`len-${profile.name}`}
              type="number"
              min="1"
              step="1"
              value={draft?.list_length ?? 5}
              onchange={(e) => {
                if (draft) draft.list_length = Math.max(1, Number(e.currentTarget.value) || 1);
                touched();
              }}
            />
          </label>
          <label class:off={settingsAbsent}>
            <span>Price sensitivity</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              disabled={settingsAbsent}
              value={draft?.price_sensitivity ?? 0}
              onchange={(e) => {
                if (draft) draft.price_sensitivity = Number(e.currentTarget.value);
                touched();
              }}
            />
          </label>
          <label class:off={settingsAbsent}>
            <span>Experience weight</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              disabled={settingsAbsent}
              value={draft?.experience_weight ?? 0}
              onchange={(e) => {
                if (draft) draft.experience_weight = Number(e.currentTarget.value);
                touched();
              }}
            />
          </label>
        </div>

        {#if settingsAbsent}
          <p class="muted small">
            Price sensitivity and experience weight need the profile settings route, which this
            server does not have yet. Everything else here writes through the routes it does have.
          </p>
        {/if}
      {/if}
    </div>

    <!-- RIGHT: the list those controls produce ---------------------------- -->
    <div class="list">
      <div class="listhead">
        <span class="label">
          {!engaged ? 'Shipping now' : previewRows ? 'Preview' : 'Would ship'}
          {#if previewing}<span class="spinner" role="status" aria-label="previewing"></span>{/if}
        </span>
        <label class="auto">
          <input
            type="checkbox"
            checked={draft?.auto_apply ?? false}
            disabled={busy === 'auto'}
            onchange={(e) => void toggleAuto(e.currentTarget.checked)}
          />
          <span>auto-apply</span>
        </label>
        <button
          type="button"
          class="primary"
          onclick={() => void apply()}
          disabled={busy === 'apply' || loading}
        >
          {busy === 'apply' ? 'Applying…' : 'Apply'}
        </button>
        {#if dirty}
          <button type="button" class="link" onclick={discard}>Discard</button>
        {/if}
      </div>

      {#if !engaged && !loading}
        <p class="muted small hintline">
          What this seat is serving. Move a control to see what would change.
        </p>
      {/if}

      <ol class="live" class:idle={!engaged}>
        {#each shipping as row (row.id)}
          <li
            class:lead={row.rank === 1}
            class:pinned={row.status === 'pinned'}
            animate:flip={{ duration: duration(200, $reducedMotion) }}
          >
            <span class="pos num">{row.rank}</span>
            <span class="id mono">
              {row.id}
              {#if row.local_ids.length}<span class="local mono">{row.local_ids[0]}</span>{/if}
            </span>
            <span class="score num">{engaged ? row.score.toFixed(3) : '—'}</span>
            {#if shippedAt.has(row.id)}
              <span class="was" title="where it sits on the gateway now">
                #{shippedAt.get(row.id)}
              </span>
            {:else}
              <span class="was new">new</span>
            {/if}
            <span class="acts">
              <button
                type="button"
                disabled={statusAbsent || busy === `status:${row.id}`}
                title={statusAbsent ? 'this server has no per-model status route yet' : 'hold this model at the top'}
                onclick={() => void setStatus(row.id, row.status === 'pinned' ? 'active' : 'pinned')}
              >
                {row.status === 'pinned' ? 'unpin' : 'pin'}
              </button>
              <button
                type="button"
                disabled={statusAbsent || busy === `status:${row.id}`}
                title={statusAbsent ? 'this server has no per-model status route yet' : 'keep this model off this seat'}
                onclick={() => void setStatus(row.id, 'removed')}
              >
                remove
              </button>
            </span>
          </li>
        {:else}
          <li class="muted small">{loading ? 'Loading…' : 'Nothing ranks for this profile yet.'}</li>
        {/each}
      </ol>

      {#if dropping.length}
        <ol class="shipped" aria-label="on the gateway now, not in this draft">
          {#each dropping as gone (gone.id)}
            <li>
              <span class="pos num">#{gone.was}</span>
              <span class="id mono">{gone.id}</span>
              <span class="was">was shipping</span>
            </li>
          {/each}
        </ol>
      {/if}

      {#if said}
        <p class={said.ok ? 'notice' : 'error'}>{said.text}</p>
      {/if}
    </div>
  </div>

  {#if open && draft && settings}
    <ProfileDetail
      {profile}
      {draft}
      {settingsAbsent}
      {ranking}
      {token}
      {defaults}
      onchange={touched}
      onsaved={(message) => (said = { ok: true, text: message })}
      onfailed={(message) => (said = { ok: false, text: message })}
      {onchanged}
    />
  {/if}
</li>

<style>
  .row {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.7rem 0.85rem;
    list-style: none;
  }
  .row.open {
    border-color: var(--accent);
  }
  .three {
    display: grid;
    grid-template-columns: minmax(0, 13rem) minmax(0, 1.15fr) minmax(0, 1.35fr);
    gap: 1rem;
    align-items: start;
  }

  /* ---- left: the card ---- */
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }
  .cardhit {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    text-align: left;
    background: none;
    border: none;
    padding: 0;
    color: inherit;
    font: inherit;
    cursor: pointer;
    min-width: 0;
  }
  .name {
    font-family: var(--display);
    font-size: 1.05rem;
    overflow-wrap: anywhere;
  }
  .cardhit:hover .name {
    color: var(--accent);
  }
  .purpose {
    color: var(--muted);
    font-size: 0.76rem;
    line-height: 1.4;
  }
  .modality {
    color: var(--muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  .chip {
    align-self: flex-start;
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--panel2);
    color: var(--muted);
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
  }
  .chip[data-tone='accent'] {
    color: var(--accent);
    border-color: var(--accent);
  }
  .chip[data-tone='good'] {
    color: var(--good);
  }

  /* ---- middle: the controls ---- */
  .controls {
    min-width: 0;
  }
  .more {
    background: none;
    border: none;
    color: var(--accent);
    font: inherit;
    font-size: 0.72rem;
    padding: 0.15rem 0;
    cursor: pointer;
    text-decoration: underline;
  }
  .sum {
    color: var(--muted);
    font-size: 0.7rem;
    margin: 0.3rem 0 0;
  }
  .numbers {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
    gap: 0.4rem 0.6rem;
    margin-top: 0.5rem;
  }
  .numbers label {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    font-size: 0.72rem;
    color: var(--muted);
    min-width: 0;
  }
  .numbers label.off {
    opacity: 0.55;
  }
  .numbers input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font: inherit;
    font-size: 0.78rem;
    padding: 0.2rem 0.35rem;
    min-width: 0;
  }

  /* ---- right: the list ---- */
  .list {
    min-width: 0;
  }
  .listhead {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    margin-bottom: 0.35rem;
  }
  .listhead .label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--muted);
    margin-right: auto;
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .spinner {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    border: 1.5px solid var(--rule);
    border-top-color: var(--accent);
    animation: spin 700ms linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .auto {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.72rem;
    color: var(--muted);
    cursor: pointer;
  }
  ol.live,
  ol.shipped {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  ol.live li {
    display: grid;
    grid-template-columns: 1.5rem minmax(0, 1fr) 3.2rem 2.6rem auto;
    gap: 0.45rem;
    align-items: center;
    padding: 0.22rem 0.3rem;
    border-bottom: 1px solid var(--rule);
  }
  ol.live li.lead {
    background: color-mix(in oklab, var(--accent) 8%, transparent);
    border-radius: 6px;
  }
  /* the "nothing here" line is a sentence, not a row: it gets the whole width */
  ol.live li.small {
    display: block;
    border-bottom: none;
  }
  ol.live li.pinned .pos {
    color: var(--accent);
  }
  /* the shipped list, before anything has been asked of the server */
  ol.live.idle li {
    opacity: 0.62;
  }
  ol.live.idle li.lead {
    background: none;
  }
  .hintline {
    margin: 0 0 0.3rem;
  }
  ol.shipped {
    opacity: 0.45;
    margin-top: 0.3rem;
  }
  ol.shipped li {
    display: grid;
    grid-template-columns: 2rem minmax(0, 1fr) auto;
    gap: 0.45rem;
    align-items: center;
    padding: 0.18rem 0.3rem;
    font-size: 0.74rem;
  }
  .pos {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .id {
    font-size: 0.78rem;
    overflow-wrap: anywhere;
  }
  .local {
    display: block;
    color: var(--reach);
    font-size: 0.66rem;
    overflow-wrap: anywhere;
  }
  .score {
    text-align: right;
    font-size: 0.76rem;
  }
  .was {
    color: var(--muted);
    font-size: 0.68rem;
    text-align: right;
  }
  .was.new {
    color: var(--accent);
  }
  /*
    Always in the DOM, shown on hover and whenever anything inside has focus.
    Rendering them only on hover would put them out of reach of the keyboard,
    which is the usual way a hover affordance quietly excludes people.
  */
  .acts {
    display: inline-flex;
    gap: 0.2rem;
    opacity: 0;
    transition: opacity 120ms ease;
  }
  ol.live li:hover .acts,
  ol.live li:focus-within .acts {
    opacity: 1;
  }
  .acts button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 5px;
    color: var(--muted);
    font: inherit;
    font-size: 0.66rem;
    padding: 0.02rem 0.3rem;
    cursor: pointer;
  }
  .acts button:hover:not(:disabled) {
    color: var(--ink);
    border-color: var(--accent);
  }

  /* ---- shared ---- */
  button.primary,
  button.link {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    font-size: 0.74rem;
    padding: 0.15rem 0.6rem;
    cursor: pointer;
  }
  button.primary {
    border-color: var(--accent);
  }
  button.link {
    background: none;
    border-color: transparent;
    color: var(--muted);
    text-decoration: underline;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 0.74rem;
    line-height: 1.45;
  }
  .notice {
    color: var(--good);
    font-size: 0.74rem;
    margin: 0.35rem 0 0;
  }
  .error {
    color: var(--bad);
    font-size: 0.74rem;
    margin: 0.35rem 0 0;
    overflow-wrap: anywhere;
  }

  @media (max-width: 1100px) {
    .three {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }
    .list {
      grid-column: 1 / -1;
    }
  }
  @media (max-width: 700px) {
    .three {
      grid-template-columns: minmax(0, 1fr);
    }
    .list {
      grid-column: auto;
    }
    .acts {
      opacity: 1;
    }
  }
</style>
