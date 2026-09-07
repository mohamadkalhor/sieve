<script lang="ts">
  import type { Decision } from '$lib/types';

  interface Props {
    decisions: Decision[];
  }
  let { decisions }: Props = $props();

  const tone: Record<string, string> = {
    switch: 'accent',
    suspend: 'bad',
    hold: 'muted',
    weights: 'reach',
    policy: 'reach',
    apply: 'good',
    pull: 'muted'
  };
</script>

<ol class="timeline">
  {#each decisions as decision (decision.id)}
    <li>
      <span class="pill" style:color={`var(--${tone[decision.kind] ?? 'muted'})`}>{decision.kind}</span>
      <span class="reason">{decision.reason}</span>
      <span class="meta mono">
        {decision.actor} · {new Date(decision.at).toLocaleString(undefined, {
          dateStyle: 'short',
          timeStyle: 'short'
        })}
      </span>
    </li>
  {/each}
</ol>

<style>
  .timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  li {
    display: grid;
    grid-template-columns: 5.5rem 1fr auto;
    gap: 0.75rem;
    align-items: baseline;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--rule);
  }
  .pill {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .reason {
    overflow-wrap: anywhere;
  }
  .meta {
    color: var(--muted);
    font-size: 0.72rem;
    white-space: nowrap;
  }
  @media (max-width: 700px) {
    li {
      grid-template-columns: 1fr;
      gap: 0.15rem;
    }
  }
</style>
