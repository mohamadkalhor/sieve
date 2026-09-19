<script lang="ts">
  /**
   * Loading rows (CONSOLE.md section 3.3).
   *
   * A list that is still arriving is not an empty list. Reusing the empty state
   * for loading is how "nothing shipped yet" gets read as a fact while the
   * request is still in flight, so loading has a shape of its own.
   */
  interface Props {
    rows?: number;
    height?: number;
    class?: string;
  }

  let { rows = 6, height = 40, class: klass = '' }: Props = $props();
</script>

<div class="sk {klass}" role="status" aria-label="Loading" style="--sk-h: {height}px">
  {#each Array.from({ length: rows }, (_, i) => i) as i (i)}
    <div class="row"></div>
  {/each}
</div>

<style>
  .sk {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .row {
    height: var(--sk-h);
    border-radius: var(--r-2);
    background: linear-gradient(90deg, var(--c-panel) 0%, var(--c-raised) 50%, var(--c-panel) 100%);
    background-size: 200% 100%;
    animation: sweep 1.4s ease-in-out infinite;
  }
  @keyframes sweep {
    from {
      background-position: 200% 0;
    }
    to {
      background-position: -200% 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .row {
      animation: none;
    }
  }
</style>
