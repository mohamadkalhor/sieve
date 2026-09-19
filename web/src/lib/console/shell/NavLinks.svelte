<script lang="ts">
  /**
   * The eight sections, in the top bar (CONSOLE.md section 6.1).
   *
   * `Seats` replaces the old `Profiles`: a profile is now the stored half of a
   * seat, and the list of them belongs beside the workspace they open, not
   * among the sections.
   *
   * Active is `aria-current="page"`, not a colour: the colour follows from it,
   * and a reader who cannot see the colour still knows where they are.
   */
  import { page } from '$app/stores';

  const links = [
    { href: '/seats', label: 'Seats' },
    { href: '/field', label: 'Field' },
    { href: '/sources', label: 'Sources' },
    { href: '/unscored', label: 'Unscored' },
    { href: '/connectors', label: 'Connectors' },
    { href: '/runs', label: 'Runs' },
    { href: '/axes', label: 'Axes' },
    { href: '/guide', label: 'Guide' }
  ];

  const current = $derived($page.url.pathname);
  /** `/seats/foo` still lights `Seats`. */
  const isActive = (href: string) => current === href || current.startsWith(`${href}/`);
</script>

<ul class="links">
  {#each links as link (link.href)}
    <li>
      <a
        class="link"
        href={link.href}
        aria-current={isActive(link.href) ? 'page' : undefined}
      >
        {link.label}
      </a>
    </li>
  {/each}
</ul>

<style>
  .links {
    display: flex;
    align-items: center;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .link {
    display: block;
    padding: 5px 9px;
    border-radius: var(--r-3);
    font-size: 13px;
    color: var(--c-muted);
    white-space: nowrap;
    text-align: left;
  }

  .link:hover {
    color: var(--c-ink);
    background: var(--c-raised);
  }

  .link[aria-current='page'] {
    color: var(--c-ink);
    background: var(--c-raised);
  }

  @media (max-width: 1200px) {
    .link {
      padding: 5px 7px;
      font-size: 12px;
    }
  }

  /* in the sheet it is a column, not a row */
  @media (max-width: 900px) {
    .links {
      flex-direction: column;
      align-items: stretch;
      gap: 1px;
    }

    .link {
      font-size: 13px;
      padding: 7px 9px;
    }
  }
</style>
