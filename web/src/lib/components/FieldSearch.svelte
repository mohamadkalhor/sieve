<script lang="ts">
  /**
   * Two boxes over the Field: a provider, then a model of that provider.
   *
   * Both offer a datalist. Nobody types `openai/gpt-5-6-terra-non-reasoning`
   * from memory, and a filter you cannot see the options of is a guessing game.
   *
   * The model box is scoped to the provider once one is set, and free when it
   * is not — so either box alone is useful, which is how people actually
   * arrive: sometimes you know the vendor, sometimes only the model.
   */
  import { families, providers } from '$lib/field';
  import type { ModelRow } from '$lib/api/client';

  interface Props {
    models: ModelRow[];
    provider: string;
    model: string;
    /** what the current pair actually matched, for the count under the boxes */
    matched: number;
    onchange: (next: { provider: string; model: string }) => void;
  }
  let { models, provider, model, matched, onchange }: Props = $props();

  const allProviders = $derived(providers(models));
  const scoped = $derived(families(models, provider.trim() || undefined));
  const active = $derived(Boolean(provider.trim() || model.trim()));

  /** Changing the provider drops a model that is no longer inside it. */
  function setProvider(next: string) {
    const stillMine = scopedFor(next).some(
      (f) => f.id === model || f.label.toLowerCase() === model.trim().toLowerCase()
    );
    onchange({ provider: next, model: stillMine ? model : '' });
  }

  function scopedFor(who: string) {
    return families(models, who.trim() || undefined);
  }
</script>

<div class="search">
  <label class="box">
    <span>Provider</span>
    <input
      list="field-providers"
      value={provider}
      placeholder="every provider"
      aria-label="Provider"
      oninput={(e) => setProvider(e.currentTarget.value)}
    />
  </label>
  <datalist id="field-providers">
    {#each allProviders as who (who)}<option value={who}></option>{/each}
  </datalist>

  <label class="box">
    <span>Model</span>
    <input
      list="field-models"
      value={model}
      placeholder={provider.trim() ? `any ${provider.trim()} model` : 'every model'}
      aria-label="Model"
      oninput={(e) => onchange({ provider, model: e.currentTarget.value })}
    />
  </label>
  <datalist id="field-models">
    {#each scoped as family (family.id)}<option value={family.id}>{family.label}</option>{/each}
  </datalist>

  {#if active}
    <!-- a filter with no way out is a trap -->
    <button type="button" onclick={() => onchange({ provider: '', model: '' })}>
      Clear
    </button>
    <span class="count" role="status">
      {matched} lit{#if matched === 0}, nothing matched that{/if}
    </span>
  {/if}
</div>

<style>
  .search {
    display: flex;
    align-items: flex-end;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin-bottom: 0.7rem;
  }
  .box {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .box span {
    color: var(--muted);
    font-size: 0.75rem;
  }
  input {
    background: var(--panel2);
    color: var(--ink);
    border: 1px solid var(--rule);
    border-radius: 7px;
    padding: 0.25rem 0.5rem;
    font: inherit;
    font-size: 0.82rem;
    min-width: 13rem;
  }
  button {
    border: 1px solid var(--rule);
    border-radius: 7px;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 0.8rem;
    padding: 0.28rem 0.7rem;
    cursor: pointer;
  }
  .count {
    color: var(--good);
    font-size: 0.78rem;
    padding-bottom: 0.3rem;
  }
  @media (max-width: 560px) {
    .box,
    input {
      min-width: 0;
      width: 100%;
    }
    .box {
      flex: 1 1 100%;
    }
  }
</style>
