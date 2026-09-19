<script lang="ts">
  /**
   * Who you are (CONSOLE.md section 6.1) -- the gate fetch and the sign-out
   * moved here from `Rail.svelte`, and the pasted-token field came with them:
   * a token is how you change something in a browser gate does not know, and
   * it is one for the whole app (`session.token`).
   *
   * "API unreachable" is not here any more. It is a reading about the server,
   * so it lives in the status bar, and the rail's version used to say it under
   * the person's own name as if they had done something wrong.
   */
  import { browser } from '$app/environment';
  import { person } from '$lib/who';
  import { session } from '$lib/session.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';

  /**
   * gate v2's own same-origin answer to "who is this" (AUTH-CONTRACT.md
   * section 4/7). A checkout with no nginx in front of it has no `/auth/*`, so
   * a failed fetch reads exactly like "not signed in" -- and gate off is a
   * normal state, not an error worth showing.
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
    if (!browser) return;
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

  let trigger = $state<HTMLElement | null>(null);
  let open = $state(false);

  /** The name gate vouches for, else the one the API knows, else nobody. */
  const label = $derived(
    gate ? person(gate.name || gate.email) : session.user ? person(session.user.name) : 'Sign in'
  );
  const role = $derived(gate?.role ?? (session.user ? 'api' : ''));
  const checked = $derived(gateChecked || session.checked);

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
</script>

<button
  class="who"
  type="button"
  bind:this={trigger}
  aria-expanded={open}
  onclick={() => (open = !open)}
>
  {#if !checked}
    <span class="name">checking…</span>
  {:else}
    <span class="name">{label}</span>
    {#if role}<span class="role">{role}</span>{/if}
  {/if}
  <Icon name="chevron" size={12} />
</button>

<Popover {open} anchor={trigger} onclose={() => (open = false)} label="Account" width={260}>
  <div class="menu">
    {#if gate}
      <p class="row">
        <span class="email" title={gate.email}>{gate.email}</span>
      </p>
      <p class="row links">
        <a href="/auth/account" rel="external">Account</a>
        {#if gate.role === 'owner'}
          <a href="/auth/admin" rel="external">Admin</a>
        {/if}
      </p>
      <a class="row out" href="/auth/logout" onclick={signOut} rel="external nofollow">Sign out</a>
    {:else if gateChecked}
      <p class="row">
        <a class="in" href="/auth/login" rel="external nofollow">Sign in</a>
      </p>
    {/if}

    <label class="row token">
      <span class="caps">Token (needed to change anything)</span>
      <input type="password" autocomplete="off" placeholder="paste a bearer token" bind:value={session.token} />
    </label>
  </div>
</Popover>

<style>
  .who {
    display: flex;
    align-items: center;
    gap: 7px;
    height: 30px;
    padding: 0 8px;
    border: 0;
    border-radius: var(--r-3);
    background: transparent;
    color: var(--c-ink-2);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    max-width: 220px;
  }

  .who:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }

  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .role {
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
  }

  .menu {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .row {
    margin: 0;
  }

  .email {
    font-size: 13px;
    color: var(--c-ink);
    overflow-wrap: anywhere;
  }

  .links {
    display: flex;
    gap: 10px;
    font-size: 13px;
    color: var(--c-muted);
  }

  .links a:hover,
  .out:hover,
  .in:hover {
    color: var(--c-accent);
  }

  .out,
  .in {
    font-size: 13px;
    color: var(--c-ink-2);
  }

  .token {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--c-rule);
  }

  .token input {
    width: 100%;
    box-sizing: border-box;
    height: 28px;
    padding: 0 8px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    background: var(--c-panel);
    color: var(--c-ink);
    font: inherit;
    font-size: 12px;
  }
</style>
