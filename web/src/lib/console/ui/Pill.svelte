<script lang="ts">
  /**
   * A need chip (CONSOLE.md section 3.3): `vision 43`. Pressed means the seat
   * requires it, so the whole chip changes colour, not just its count -- one
   * glance down the row has to say which needs are on.
   */
  interface Props {
    label: string;
    pressed: boolean;
    count?: string | null;
    title?: string;
    onclick: () => void;
    class?: string;
  }

  let { label, pressed, count = null, title = '', onclick, class: klass = '' }: Props = $props();
</script>

<button type="button" class="pill {klass}" aria-pressed={pressed} {title} {onclick}>
  {label}{#if count !== null}<span class="n">{count}</span>{/if}
</button>

<style>
  .pill {
    height: 28px;
    padding: 0 10px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-pill);
    background: transparent;
    color: var(--c-muted);
    font-family: var(--f-ui);
    font-size: 12px;
    cursor: pointer;
  }
  .n {
    font-family: var(--f-mono);
    margin-left: 6px;
  }
  .pill:hover {
    border-color: var(--c-rule-hover);
    color: var(--c-ink-2);
  }
  .pill[aria-pressed='true'] {
    border-color: var(--c-accent);
    color: var(--c-accent);
  }

  @media (max-width: 899px) {
    .pill {
      height: 44px;
    }
  }
</style>
