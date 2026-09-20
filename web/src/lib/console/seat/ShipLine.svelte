<script lang="ts">
  /**
   * The line the lineup ships above (CONSOLE.md section 6.6).
   *
   * It is the table's own row, so the labels and the rows below it share one set
   * of columns and stay aligned; the rule between the two sentences is what
   * makes the claim -- everything above it goes out, everything below it does
   * not.
   */
  interface Props {
    /** how many columns the single cell spans, for the table's semantics */
    colspan?: number;
  }

  let { colspan = 7 }: Props = $props();
</script>

<div class="ship" role="row">
  <div class="cell" role="cell" aria-colspan={colspan}>
    <span class="say caps">Ships above this line</span>
    <span class="rule"></span>
    <span class="say caps">next up below</span>
  </div>
</div>

<style>
  .ship {
    display: block;
    height: 28px;
    padding: 0 12px;
    border-bottom: 1px solid var(--c-rule-soft);
    background: var(--c-panel);
  }

  .cell {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 100%;
  }

  .say {
    color: var(--c-accent);
    white-space: nowrap;
  }

  .rule {
    flex: 1 1 auto;
    height: 1px;
    background: var(--c-accent);
  }
</style>
