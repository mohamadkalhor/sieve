<script lang="ts">
  /**
   * "Saved." -- one line under the seat header (CONSOLE.md section 3.3).
   *
   * `aria-live="polite"` because the sentence appears without the reader doing
   * anything: a save that happens out of sight has to be announced or the
   * person has to go looking for whether it worked.
   */
  interface Props {
    message: { ok: boolean; text: string } | null;
  }

  let { message = null }: Props = $props();
</script>

<div class="toast" aria-live="polite">
  {#if message}
    <span class="dot" data-ok={message.ok}></span>
    <span data-ok={message.ok}>{message.text}</span>
  {/if}
</div>

<style>
  .toast {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 18px;
    font-size: 12px;
    color: var(--c-muted);
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--c-muted);
    flex: none;
  }
  .dot[data-ok='true'] {
    background: var(--c-accent);
  }
  .dot[data-ok='false'] {
    background: var(--c-bad);
  }
  span[data-ok='false'] {
    color: var(--c-bad);
  }
</style>
