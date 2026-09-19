<script lang="ts">
  /**
   * The status bar (CONSOLE.md section 6.1).
   *
   * Empty on purpose, and not a stub that renders nothing: the row is already
   * in the shell's grid, so the layout is the final one and package F fills
   * the body -- last pull, next pull, the in-flight run, "API unreachable".
   * Until then the row is present and says nothing, which is the truth: this
   * package has nothing to report.
   */
</script>

<footer class="status" aria-label="Status"></footer>

<style>
  .status {
    display: flex;
    align-items: center;
    gap: 16px;
    height: var(--h-status);
    padding: 0 12px;
    border-top: 1px solid var(--c-rule);
    background: var(--c-bg);
    font-size: 12px;
    color: var(--c-muted);
  }
</style>
