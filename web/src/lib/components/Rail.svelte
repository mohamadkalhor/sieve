<script lang="ts">
  import { page } from '$app/stores';
  import type { StatusRow } from '$lib/api/client';
  import { freshness } from '$lib/freshness';

  interface Props {
    status?: StatusRow | null;
    /** the status request failed, as opposed to not having answered yet */
    unreachable?: boolean;
  }
  let { status = null, unreachable = false }: Props = $props();

  const links = [
    { href: '/field', label: 'Field' },
    { href: '/profiles', label: 'Profiles' },
    { href: '/rankings', label: 'Rankings' },
    { href: '/chains', label: 'Chains' },
    { href: '/sources', label: 'Sources' },
    { href: '/pulse', label: 'Pulse' }
  ];

  const current = $derived($page.url.pathname);

  /** re-rendered each minute so "12 min ago" keeps counting without a refetch */
  let now = $state(new Date());
  $effect(() => {
    const tick = setInterval(() => (now = new Date()), 30_000);
    return () => clearInterval(tick);
  });

  const fresh = $derived(freshness(status, now));
  const exact = $derived.by(() => {
    const stamp = status?.ran_at ?? status?.pulled_at;
    return stamp
      ? new Date(stamp).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
      : '';
  });
</script>

<nav aria-label="Sections">
  <a class="logo" href="/field">
    <span class="mark" aria-hidden="true"></span>
    <span class="word">Sieve</span>
  </a>

  <!--
    At the top, not in the footer. It used to sit at the bottom of the rail in
    small type, and on a phone the footer is hidden altogether -- so the one
    fact that says whether any number on the page can be trusted was the
    easiest thing on the page to miss.

    While the status has not arrived it says so. It used to print "never
    pulled" until the request returned, which is an absence rendered as a fact
    for however long the API took to answer.
  -->
  <div class="fresh" class:late={fresh.late} class:down={unreachable} title={exact} role="status">
    <span class="dot" aria-hidden="true"></span>
    <span class="text">
      {#if unreachable}
        <span class="when">status unavailable</span>
      {:else if fresh.ago === null}
        <span class="when">checking…</span>
      {:else}
        <span class="when">{fresh.late ? 'last updated' : 'updated'} {fresh.ago}</span>
        {#if fresh.cadence}<span class="cadence">{fresh.late ? 'overdue · ' : ''}{fresh.cadence}</span>{/if}
      {/if}
    </span>
  </div>

  <ul>
    {#each links as link (link.href)}
      <li>
        <a
          href={link.href}
          class:active={current.startsWith(link.href)}
          aria-current={current.startsWith(link.href) ? 'page' : undefined}
        >
          {link.label}
        </a>
      </li>
    {/each}
  </ul>

  {#if status}
    <footer>
      <div class="line">
        {status.sources_enabled} source{status.sources_enabled === 1 ? '' : 's'} on
      </div>
    </footer>
  {/if}
</nav>

<style>
  nav {
    width: var(--rail);
    min-width: var(--rail);
    height: 100dvh;
    position: sticky;
    top: 0;
    display: flex;
    flex-direction: column;
    gap: 1.1rem;
    padding: 1.25rem 0.85rem;
    border-right: 1px solid var(--rule);
    background: var(--panel);
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0 0.4rem;
  }
  .mark {
    width: 12px;
    height: 12px;
    border-radius: 3px;
    background: var(--accent);
  }
  .word {
    font-family: var(--display);
    font-size: 1.15rem;
    letter-spacing: -0.02em;
  }
  .fresh {
    display: flex;
    align-items: flex-start;
    gap: 0.45rem;
    padding: 0.45rem 0.55rem;
    border: 1px solid var(--rule);
    border-radius: 8px;
    background: var(--panel2);
    font-size: 0.72rem;
    line-height: 1.35;
  }
  .fresh .dot {
    flex: none;
    width: 7px;
    height: 7px;
    margin-top: 0.3rem;
    border-radius: 50%;
    background: var(--good);
  }
  .fresh.late .dot {
    background: var(--accent);
  }
  .fresh.down .dot {
    background: var(--muted);
  }
  .fresh .text {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .fresh .when {
    color: var(--ink);
  }
  .fresh .cadence {
    color: var(--muted);
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
    flex: 1;
  }
  li a {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.4rem;
    padding: 0.4rem 0.55rem;
    border-radius: 7px;
    color: var(--muted);
    transition: background 120ms ease, color 120ms ease;
  }
  li a:hover {
    color: var(--ink);
    background: var(--panel2);
  }
  li a.active {
    color: var(--ink);
    background: var(--panel2);
    box-shadow: inset 2px 0 0 var(--accent);
  }
  footer {
    border-top: 1px solid var(--rule);
    padding-top: 0.75rem;
    color: var(--muted);
    font-size: 0.72rem;
  }
  .line {
    padding: 0 0.55rem;
    overflow-wrap: anywhere;
  }
  @media (max-width: 700px) {
    nav {
      width: 100%;
      min-width: 0;
      height: auto;
      position: static;
      flex-direction: row;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.75rem;
      border-right: none;
      border-bottom: 1px solid var(--rule);
      padding: 0.7rem 0.9rem;
    }
    /* the status keeps its place beside the logo; the links take their own row */
    .fresh {
      margin-left: auto;
      padding: 0.3rem 0.5rem;
    }
    .fresh .cadence {
      display: none;
    }
    ul {
      flex: 1 1 100%;
      flex-direction: row;
      overflow-x: auto;
    }
    footer {
      display: none;
    }
  }
</style>
