<script lang="ts">
  interface Props {
    confidence: number;
  }
  let { confidence }: Props = $props();

  const filled = $derived(Math.round(Math.min(1, Math.max(0, confidence)) * 5));
  const label = $derived(`confidence ${(confidence * 100).toFixed(0)}%`);
</script>

<span class="dots" title={label} aria-label={label} role="img">
  {#each [0, 1, 2, 3, 4] as index (index)}
    <span class="dot" class:on={index < filled}></span>
  {/each}
</span>

<style>
  .dots {
    display: inline-flex;
    gap: 2px;
    align-items: center;
  }
  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--rule);
  }
  .dot.on {
    background: var(--reach);
  }
</style>
