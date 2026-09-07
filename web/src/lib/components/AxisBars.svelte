<script lang="ts">
  import type { AxisScore } from '$lib/types';

  interface Props {
    axes: AxisScore[];
    weights?: Record<string, number>;
  }
  let { axes, weights = {} }: Props = $props();

  const shown = $derived(
    axes.filter((axis) => (weights[axis.axis] ?? 1) > 0).sort((a, b) => a.axis.localeCompare(b.axis))
  );
</script>

<div class="bars" role="img" aria-label="per-axis contribution">
  {#each shown as axis (axis.axis)}
    {@const height = Math.round((axis.value ?? 0) * 100)}
    <span
      class="bar"
      class:cost={axis.axis === 'cost'}
      class:unmeasured={axis.value === null}
      style:height={`${Math.max(3, height)}%`}
      style:opacity={axis.coverage < 1 ? 0.45 + axis.coverage * 0.55 : 1}
      title={`${axis.axis}: ${axis.value === null ? 'unmeasured' : axis.value.toFixed(2)} (coverage ${axis.coverage.toFixed(2)})`}
    ></span>
  {/each}
</div>

<style>
  .bars {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 26px;
    min-width: 3rem;
  }
  .bar {
    width: 5px;
    border-radius: 1px 1px 0 0;
    background: var(--reach);
  }
  .bar.cost {
    background: var(--accent);
  }
  .bar.unmeasured {
    background: repeating-linear-gradient(
      45deg,
      var(--rule),
      var(--rule) 2px,
      transparent 2px,
      transparent 4px
    );
  }
</style>
