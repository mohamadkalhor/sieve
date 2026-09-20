<script lang="ts">
  /**
   * What this model costs (CONSOLE.md section 6.7).
   *
   * A token unit gets three figures, because a per-token price is only half an
   * answer without the cost of the seat's own task. A media unit gets one,
   * worded by the unit itself: three token columns about a price per image
   * would be three ways of saying nothing.
   *
   * The block never draws an absence it cannot vouch for. A failed lookup says
   * so and offers the retry; "No posted price" is reserved for the lookup that
   * came back and had nothing.
   */
  import type { ApiError } from '$lib/api/client';
  import { explainError } from '$lib/api/client';
  import Button from '../ui/Button.svelte';
  import { NO_PRICE, type Figure } from './view';

  interface Props {
    figures: Figure[];
    /** the price lookup itself failed -- not the same thing as a price nobody posted */
    error: ApiError | null;
    onretry: () => void;
  }

  let { figures, error, onretry }: Props = $props();
</script>

<section class="block" data-block="price">
  {#if error}
    <p class="warn">No price to show: {explainError(error)}</p>
    <div class="act">
      <Button variant="outline" size="sm" onclick={onretry}>Try again</Button>
    </div>
  {:else if figures.length === 0}
    <p class="none">{NO_PRICE}</p>
  {:else}
    <dl class="figures">
      {#each figures as figure (figure.label)}
        <div class="figure">
          <dt class="label">{figure.label}</dt>
          <dd class="money" title={figure.money.title}>{figure.money.text}</dd>
        </div>
      {/each}
    </dl>
  {/if}
</section>

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .figures {
    display: flex;
    gap: 24px;
    margin: 0;
  }
  .figure {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .label {
    font-size: 11px;
    color: var(--c-muted);
  }
  .money {
    margin: 0;
    font-family: var(--f-mono);
    font-size: 15px;
    color: var(--c-ink);
  }
  .none,
  .warn {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
  }
  .none {
    color: var(--c-muted);
  }
  .warn {
    color: var(--c-warn);
  }
  .act {
    display: flex;
  }
</style>
