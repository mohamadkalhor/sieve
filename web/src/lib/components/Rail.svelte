<script lang="ts">
  import { page } from '$app/stores';

  interface Props {
    lastPull?: string | null;
    sources?: number;
  }
  let { lastPull = null, sources = 0 }: Props = $props();

  const links = [
    { href: '/field', label: 'Field' },
    { href: '/profiles', label: 'Profiles' },
    { href: '/rankings', label: 'Rankings' },
    { href: '/chains', label: 'Chains' },
    { href: '/sources', label: 'Sources' },
    { href: '/pulse', label: 'Pulse', soon: true }
  ];

  const current = $derived($page.url.pathname);
  const when = $derived(
    lastPull ? new Date(lastPull).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : null
  );
</script>

<nav aria-label="Sections">
  <a class="logo" href="/field">
    <span class="mark" aria-hidden="true"></span>
    <span class="word">Sieve</span>
  </a>

  <ul>
    {#each links as link (link.href)}
      <li>
        <a
          href={link.soon ? undefined : link.href}
          class:active={current.startsWith(link.href)}
          class:soon={link.soon}
          aria-current={current.startsWith(link.href) ? 'page' : undefined}
          aria-disabled={link.soon ? 'true' : undefined}
        >
          {link.label}
          {#if link.soon}<span class="tag">phase 2</span>{/if}
        </a>
      </li>
    {/each}
  </ul>

  <footer>
    <div class="line">{sources} source{sources === 1 ? '' : 's'}</div>
    <div class="line mono">{when ? `pulled ${when}` : 'never pulled'}</div>
  </footer>
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
    gap: 1.5rem;
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
  li a:hover:not(.soon) {
    color: var(--ink);
    background: var(--panel2);
  }
  li a.active {
    color: var(--ink);
    background: var(--panel2);
    box-shadow: inset 2px 0 0 var(--accent);
  }
  li a.soon {
    opacity: 0.45;
    cursor: default;
  }
  .tag {
    font-size: 0.62rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
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
      align-items: center;
      gap: 0.75rem;
      overflow-x: auto;
      border-right: none;
      border-bottom: 1px solid var(--rule);
    }
    ul {
      flex-direction: row;
      flex: 1;
    }
    footer {
      display: none;
    }
  }
</style>
