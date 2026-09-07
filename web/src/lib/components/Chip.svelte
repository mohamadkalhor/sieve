<script lang="ts">
  interface Props {
    label: string;
    value?: string | number | boolean | null | undefined;
    tone?: 'muted' | 'accent' | 'reach' | 'warn';
    title?: string;
  }
  let { label, value = null, tone = 'muted', title = '' }: Props = $props();
  const shown = $derived(value === true ? 'yes' : value === false ? 'no' : value);
</script>

<span class="chip" data-tone={tone} {title}>
  <span class="k">{label}</span>
  {#if shown !== null && shown !== ''}<span class="v num">{shown}</span>{/if}
</span>

<style>
  .chip {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35rem;
    padding: 0.15rem 0.5rem;
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--panel2);
    font-size: 0.72rem;
    white-space: nowrap;
  }
  .k {
    color: var(--muted);
  }
  .v {
    color: var(--ink);
  }
  .chip[data-tone='accent'] .v {
    color: var(--accent);
  }
  .chip[data-tone='reach'] .v {
    color: var(--reach);
  }
  .chip[data-tone='warn'] .v {
    color: var(--warn);
  }
</style>
