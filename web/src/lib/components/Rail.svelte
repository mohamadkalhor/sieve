<script lang="ts">
  /**
   * The side rail. One component, used by the layout every page sits inside,
   * so there is exactly one list of sections and one set of widths.
   *
   * Two things were wrong with it. The items differed from page to page
   * because several screens drew their own nav, and the ribbon stopped in the
   * middle of a long page: the `<nav>` was `height: 100dvh` and sticky, so its
   * border ended one screen down while the page kept going. The fix is a
   * wrapper that is as tall as the page -- it carries the border and the
   * background -- with the sticky column inside it.
   *
   * Below 900px it is a top bar with a menu button, because a 168px column
   * beside a 390px page leaves no page.
   */
  import { page } from '$app/stores';
  import { person } from '$lib/who';

  interface Props {
    /** the status request failed, as opposed to not having answered yet */
    unreachable?: boolean;
  }
  let { unreachable = false }: Props = $props();

  /**
   * gate v2's own answer to "who is this", same-origin (AUTH-CONTRACT.md
   * section 4/7): `{user_id,email,name,role,status,app,csrf}` or 401. This is
   * a different question from Sieve's own `/v1/me` (which `session` already
   * asks, for API scopes) -- the chip, the Admin link and the csrf a sign-out
   * needs all come from gate, because gate is who actually knows the person.
   *
   * A box with no nginx in front of it (every local dev checkout) has no
   * `/auth/*` at all, so a failed fetch reads exactly like "not signed in":
   * there is nothing here to fall back to, and gate off is a normal state.
   */
  interface GateMe {
    user_id: number | string;
    email: string;
    name: string;
    role: 'owner' | 'member' | 'viewer';
    status: string;
    csrf: string;
  }
  let gate = $state<GateMe | null>(null);
  let gateChecked = $state(false);

  $effect(() => {
    let alive = true;
    void (async () => {
      try {
        const reply = await fetch('/auth/me', { credentials: 'same-origin' });
        const body = reply.ok ? ((await reply.json()) as GateMe) : null;
        if (alive) gate = body;
      } catch {
        if (alive) gate = null;
      } finally {
        if (alive) gateChecked = true;
      }
    })();
    return () => {
      alive = false;
    };
  });

  async function signOut(event: Event): Promise<void> {
    event.preventDefault();
    if (gate?.csrf) {
      try {
        await fetch('/auth/logout', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'content-type': 'application/x-www-form-urlencoded' },
          body: `csrf_token=${encodeURIComponent(gate.csrf)}`
        });
      } catch {
        // Falling through to the redirect either way: a sign-out that could
        // not reach gate should still land the person on the login page
        // rather than leaving them looking signed in.
      }
    }
    window.location.href = '/auth/login';
  }

  /**
   * The sections, in this order, everywhere. Sources and Pulse are still
   * there -- the Guide links to them -- but they are readings, not places you
   * work, and a rail that lists everything lists nothing.
   */
  const links = [
    { href: '/field', label: 'Overview' },
    { href: '/profiles', label: 'Profiles' },
    { href: '/axes', label: 'Axes' },
    { href: '/unscored', label: 'Unscored' },
    { href: '/connectors', label: 'Connectors' },
    { href: '/runs', label: 'Runs' },
    { href: '/guide', label: 'Guide' }
  ];

  const current = $derived($page.url.pathname);
  const isActive = (href: string) =>
    current === href || current.startsWith(`${href}/`) || (href === '/field' && current === '/');

  /**
   * Same-origin now (gate v2, AUTH-CONTRACT.md section 4): sieve's own gate
   * instance serves `/auth/*` on sieve's own hostname, so there is no other
   * host to allow-list and no cross-origin link to build.
   */
  const signInHref = $derived(
    `/auth/login?next=${encodeURIComponent($page.url.pathname + $page.url.search + $page.url.hash)}`
  );

  /** A record like `gate:me@example.com` or a bare email: show the person. */
  const who = $derived(person(gate?.name || gate?.email));
  const role = $derived(gate?.role ?? '');

  let open = $state(false);
  $effect(() => {
    // a tap on a link closes the menu; the pathname changing is that tap
    if (current) open = false;
  });
</script>

<div class="rail">
  <nav aria-label="Sections">
    <div class="bar">
      <a class="logo" href="/field">
        <span class="mark" aria-hidden="true"></span>
        <span class="word">Sieve</span>
      </a>
      <button
        type="button"
        class="menu"
        aria-expanded={open}
        aria-controls="rail-links"
        onclick={() => (open = !open)}
      >
        <span aria-hidden="true">☰</span>
        <span class="sr">Sections</span>
      </button>
    </div>

    <ul id="rail-links" class:open>
      {#each links as link (link.href)}
        <li>
          <a
            href={link.href}
            class:active={isActive(link.href)}
            aria-current={isActive(link.href) ? 'page' : undefined}
          >
            {link.label}
          </a>
        </li>
      {/each}
    </ul>

    <div class="who" class:down={unreachable}>
      {#if gate}
        <p class="name" title={gate.email}>{who}</p>
        <p class="role">{role}</p>
        <p class="links">
          <a href="/auth/account" rel="external">Account</a>
          {#if gate.role === 'owner'}
            &middot; <a href="/auth/admin" rel="external">Admin</a>
          {/if}
        </p>
        <a class="out" href="/auth/logout" onclick={signOut} rel="external nofollow">Sign out</a>
      {:else if gateChecked}
        <a class="in" href={signInHref} rel="external nofollow">Sign in</a>
      {:else}
        <p class="role">checking…</p>
      {/if}
      {#if unreachable}
        <p class="role warn">API unreachable</p>
      {/if}
    </div>
  </nav>
</div>

<style>
  /*
    The wrapper is a flex item of the shell and stretches to the full height of
    the page, so the border and the panel colour run all the way down however
    long the page is. The nav inside it is what sticks.
  */
  .rail {
    width: var(--rail);
    min-width: var(--rail);
    border-right: 1px solid var(--rule);
    background: var(--panel);
  }
  nav {
    position: sticky;
    top: 0;
    min-height: 100vh;
    height: 100dvh;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    padding: 1.25rem 0.85rem;
    box-sizing: border-box;
  }
  .bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
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
  .menu {
    display: none;
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    font: inherit;
    padding: 0.25rem 0.5rem;
    cursor: pointer;
  }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
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
    display: block;
    padding: 0.4rem 0.55rem;
    border-radius: 7px;
    color: var(--muted);
    transition:
      background 120ms ease,
      color 120ms ease;
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
  .who {
    margin-top: auto;
    border-top: 1px solid var(--rule);
    padding: 0.55rem 0.55rem 0;
    font-size: 0.74rem;
    min-width: 0;
  }
  .name {
    margin: 0;
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .role {
    margin: 0;
    color: var(--muted);
    font-size: 0.7rem;
  }
  .role.warn {
    color: var(--warn);
  }
  .links {
    margin: 0.25rem 0 0;
    color: var(--muted);
  }
  .links a {
    color: var(--muted);
    padding: 0;
  }
  .links a:hover {
    color: var(--accent);
    background: none;
  }
  .out,
  .in {
    display: inline-block;
    margin-top: 0.25rem;
    color: var(--muted);
    cursor: pointer;
  }
  .out:hover,
  .in:hover {
    color: var(--accent);
  }

  /* ---- the top bar, below 900px ---------------------------------------- */
  @media (max-width: 900px) {
    .rail {
      width: 100%;
      min-width: 0;
      border-right: none;
      border-bottom: 1px solid var(--rule);
    }
    nav {
      position: static;
      min-height: 0;
      height: auto;
      gap: 0.5rem;
      padding: 0.7rem 0.9rem;
    }
    .menu {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
    }
    ul {
      display: none;
    }
    ul.open {
      display: flex;
      flex-direction: column;
    }
    .who {
      margin-top: 0;
      display: flex;
      align-items: baseline;
      gap: 0.5rem;
      padding-top: 0.45rem;
    }
  }
</style>
