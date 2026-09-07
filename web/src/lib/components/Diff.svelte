<script lang="ts">
  interface Props {
    before: string[];
    after: string[];
    target: string;
  }
  let { before, after, target }: Props = $props();

  const rows = $derived.by(() => {
    const seen = [...new Set([...before, ...after])];
    return seen.map((id) => {
      const was = before.indexOf(id);
      const now = after.indexOf(id);
      const state =
        was === -1 ? 'added' : now === -1 ? 'removed' : was === now ? 'same' : 'moved';
      return { id, was, now, state };
    });
  });
  const changed = $derived(rows.some((row) => row.state !== 'same'));
</script>

<div class="diff">
  <div class="head mono">{target}{changed ? '' : ' · unchanged'}</div>
  {#each rows as row (row.id)}
    <div class="row" data-state={row.state}>
      <span class="sign">{row.state === 'added' ? '+' : row.state === 'removed' ? '-' : row.state === 'moved' ? '~' : '='}</span>
      <span class="id mono">{row.id}</span>
      <span class="pos mono">
        {row.state === 'moved' ? `${row.was} → ${row.now}` : row.state === 'removed' ? row.was : row.now}
      </span>
    </div>
  {/each}
</div>

<style>
  .diff {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: hidden;
  }
  .head {
    padding: 0.4rem 0.7rem;
    border-bottom: 1px solid var(--rule);
    color: var(--muted);
    font-size: 0.75rem;
  }
  .row {
    display: grid;
    grid-template-columns: 1.2rem 1fr auto;
    gap: 0.5rem;
    padding: 0.25rem 0.7rem;
    font-size: 0.8rem;
  }
  .sign {
    color: var(--muted);
  }
  .row[data-state='added'] .sign,
  .row[data-state='added'] .id {
    color: var(--good);
  }
  .row[data-state='removed'] .sign,
  .row[data-state='removed'] .id {
    color: var(--bad);
  }
  .row[data-state='moved'] .sign,
  .row[data-state='moved'] .id {
    color: var(--accent);
  }
  .row[data-state='same'] .id {
    color: var(--muted);
  }
  .pos {
    color: var(--muted);
  }
</style>
