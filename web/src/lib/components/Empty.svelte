<script lang="ts">
  import type { ApiError } from '$lib/api/client';
  import { explainError } from '$lib/api/client';

  interface Props {
    error?: ApiError | null;
    title?: string;
    hint?: string;
  }
  let { error = null, title = 'Nothing here yet', hint = '' }: Props = $props();
</script>

<div class="empty" role="status">
  <h3>{error ? (error.code === 'not_built' ? 'Not built yet' : 'Nothing to show') : title}</h3>
  <p>{error ? explainError(error) : hint}</p>
  {#if !error && hint === ''}
    <p class="mono">Try <code>sieve pull openrouter</code>, then <code>sieve plan --store</code>.</p>
  {/if}
</div>

<style>
  .empty {
    border: 1px dashed var(--rule);
    border-radius: var(--radius);
    padding: 2rem 1.5rem;
    text-align: center;
    color: var(--muted);
    background: var(--panel);
  }
  h3 {
    font-family: var(--display);
    font-weight: 300;
    color: var(--ink);
    margin: 0 0 0.4rem;
    font-size: 1.1rem;
  }
  p {
    margin: 0.2rem 0;
    max-width: 46ch;
    margin-inline: auto;
    overflow-wrap: anywhere;
  }
  code {
    color: var(--ink);
    background: var(--panel2);
    padding: 0.05rem 0.3rem;
    border-radius: 4px;
  }
</style>
