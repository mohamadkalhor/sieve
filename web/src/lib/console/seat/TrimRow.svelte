<script lang="ts">
  /**
   * Per-router trim (CONSOLE.md section 6.5).
   *
   * Collapsed by default: most seats never touch it, and an open list of every
   * router prefix would push the table off the screen. The count on the button
   * is how many prefixes are not 1, so a trim nobody remembers setting is
   * still visible while the row is closed.
   */
  import { setTrim } from '$lib/console/logic/settings';
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Button from '$lib/console/ui/Button.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let open = $state(false);

  const settings = $derived(session.settings);
  const own = $derived(settings?.prefixWeights ?? {});
  const trimmed = $derived(Object.values(own).filter((value) => value !== 1).length);

  function commit(prefix: string, value: string): void {
    if (!settings) return;
    session.edit(setTrim(settings, prefix, value));
  }
</script>

<div class="trim">
  <Button variant="ghost" size="sm" onclick={() => (open = !open)}>
    <span class="label">Trim routers{trimmed ? ` · ${trimmed}` : ''}</span>
    <span class="chev" class:open><Icon name="chevron" size={12} /></span>
  </Button>

  {#if open}
    {#if session.prefixes.length}
      <ul class="list">
        {#each session.prefixes as prefix (prefix)}
          {@const value = own[prefix]}
          <li>
            <span class="prefix">{prefix}</span>
            <input
              class="num"
              type="number"
              min="0"
              step="0.05"
              placeholder="1"
              aria-label={`Weight for ${prefix} on this seat`}
              value={value ?? ''}
              onchange={(event) => commit(prefix, event.currentTarget.value)}
            />
          </li>
        {/each}
      </ul>
    {:else}
      <p class="note">no router prefixes are reachable yet</p>
    {/if}
    <p class="note">
      multiplies the score of every model that router serves; the best router counts.
    </p>
  {/if}
</div>

<style>
  .trim {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .label {
    font-size: 12px;
  }

  .chev {
    display: inline-flex;
    margin-left: 4px;
    transition: transform 120ms ease;
  }

  .chev.open {
    transform: rotate(90deg);
  }

  .list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .list li {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .prefix {
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-ink-2);
  }

  .num {
    width: 72px;
    padding: 3px 6px;
    border: 1px solid var(--c-rule);
    border-radius: var(--r-2);
    background: var(--c-pane);
    color: var(--c-ink);
    font-family: var(--f-mono);
    font-size: 12px;
    text-align: right;
  }

  .note {
    margin: 0;
    font-size: 11px;
    line-height: 1.4;
    color: var(--c-muted);
  }
</style>
