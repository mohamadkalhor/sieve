<script lang="ts">
  /**
   * One profile, as a row on the list: what it is, what it cares about, and
   * what it is shipping now.
   *
   * The row used to carry the tuning as well -- every slider, a list length, a
   * floor, a price sensitivity, an experience weight, pins and an Apply button
   * -- which meant the list was twenty-two copies of an editor. The editor is
   * `/profiles/<name>` now, and half of what it held does not exist any more,
   * so the row is a row: it says enough to pick one, and costs a chain to draw.
   *
   * A ranking on this box is most of a megabyte, and there are twenty-two
   * seats. Nothing here fetches one.
   */
  import { api } from '$lib/api/client';
  import type { Chain, Profile } from '$lib/types';
  import { ago } from '$lib/freshness';

  interface Props {
    profile: Profile;
    /** a token with `profiles:write` and `apply`, shared by the whole screen */
    token: string;
    /** ticks once a minute, so "shipped 51 min ago" keeps counting */
    now: Date;
  }
  let { profile, token, now }: Props = $props();

  /** how many axes a row names before it says "and two more" */
  const SHOWN = 4;
  /** how many shipped models a row lists; the page has the rest */
  const TOP = 5;

  let chain = $state<Chain | null>(null);
  let loading = $state(true);

  const options = $derived({ token: token || undefined });
  const href = $derived(`/profiles/${encodeURIComponent(profile.name)}`);

  const weights = $derived(
    Object.entries(profile.weights)
      .filter(([, weight]) => weight > 0)
      .sort(([aAxis, a], [bAxis, b]) => b - a || aAxis.localeCompare(bAxis))
  );
  const shown = $derived(weights.slice(0, SHOWN));
  const rest = $derived(weights.length - shown.length);
  const shipped = $derived(chain ? [chain.primary, ...(chain.fallbacks ?? [])] : []);

  $effect(() => {
    const wanted = profile.name;
    loading = true;
    void (async () => {
      const result = await api.chain(wanted, options);
      if (wanted !== profile.name) return;
      chain = result.ok ? result.value : null;
      loading = false;
    })();
  });
</script>

<li class="row">
  <div class="who">
    <a class="name" {href}>{profile.name}</a>
    <p class="purpose">{profile.purpose}</p>
    <div class="axes">
      {#each shown as [axis, weight] (axis)}
        <span class="axis"><span class="mono">{weight.toFixed(2)}</span> {axis}</span>
      {/each}
      {#if rest > 0}<span class="axis more">and {rest} more</span>{/if}
    </div>
  </div>

  <div class="ships">
    <div class="head">
      <span class="label">ships {profile.ship ?? shipped.length}</span>
      {#if chain}<span class="when">{ago(new Date(chain.computed_at), now)}</span>{/if}
    </div>
    {#if loading}
      <p class="quiet">…</p>
    {:else if shipped.length === 0}
      <p class="quiet">not shipped yet</p>
    {:else}
      <ol>
        {#each shipped.slice(0, TOP) as id, index (id)}
          <li><span class="mono pos">{index + 1}</span> <span class="mono id">{id}</span></li>
        {/each}
      </ol>
      {#if shipped.length > TOP}
        <a class="all" {href}>and {shipped.length - TOP} more</a>
      {/if}
    {/if}
  </div>

  <a class="tune" {href}>Tune →</a>
</li>

<style>
  .row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 20rem auto;
    gap: 1.25rem;
    align-items: start;
    padding: 1rem 0;
    border-top: 1px solid var(--rule);
  }
  .mono {
    font-family: var(--mono);
  }
  .who {
    min-width: 0;
  }
  .name {
    font-family: var(--display);
    font-size: 1.15rem;
    font-weight: 500;
    color: var(--ink);
    text-decoration: none;
  }
  .name:hover {
    color: var(--accent);
  }
  .purpose {
    color: var(--muted);
    font-size: 0.85rem;
    margin: 0.2rem 0 0.5rem;
    max-width: 62ch;
  }
  .axes {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 0.9rem;
    font-size: 0.78rem;
    color: var(--muted);
  }
  .axis .mono {
    color: var(--ink);
  }
  .more {
    font-style: italic;
  }

  .ships {
    min-width: 0;
  }
  .all {
    display: inline-block;
    margin-top: 0.3rem;
    font-size: 0.78rem;
    color: var(--muted);
    text-decoration: none;
  }
  .all:hover {
    color: var(--accent);
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.6rem;
    font-size: 0.72rem;
    color: var(--muted);
    margin-bottom: 0.3rem;
  }
  .ships ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .pos {
    color: var(--accent);
    font-size: 0.72rem;
  }
  .id {
    font-size: 0.76rem;
  }
  .quiet {
    color: var(--muted);
    font-size: 0.8rem;
    margin: 0;
  }

  .tune {
    align-self: center;
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: var(--panel2);
    color: var(--ink);
    font-size: 0.8rem;
    padding: 0.4rem 0.8rem;
    text-decoration: none;
    white-space: nowrap;
  }
  .tune:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  @media (max-width: 900px) {
    .row {
      grid-template-columns: minmax(0, 1fr);
      gap: 0.7rem;
    }
    .tune {
      justify-self: start;
    }
  }
</style>
