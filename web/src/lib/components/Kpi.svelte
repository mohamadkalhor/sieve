<script lang="ts">
  interface Props {
    label: string;
    value: number | string;
    hint?: string;
    tone?: 'ink' | 'accent' | 'reach' | 'muted';
  }
  let { label, value, hint = '', tone = 'ink' }: Props = $props();
</script>

<div class="kpi">
  <div class="value num" style:color={`var(--${tone})`}>{value}</div>
  <div class="label">{label}</div>
  {#if hint}<div class="hint">{hint}</div>{/if}
</div>

<style>
  .kpi {
    padding: 0.7rem 0.9rem;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    min-width: 7.5rem;
  }
  .value {
    font-size: 1.5rem;
    line-height: 1.1;
  }
  .label {
    color: var(--muted);
    font-size: 0.75rem;
    margin-top: 0.15rem;
  }
  .hint {
    color: var(--muted);
    font-size: 0.68rem;
    opacity: 0.8;
  }
</style>
