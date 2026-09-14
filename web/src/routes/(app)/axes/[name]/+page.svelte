<script lang="ts">
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { api, explainError, type SourceFieldRow, type SourceRow } from '$lib/api/client';
  import type { Axis, Modality } from '$lib/types';

  const requested = $derived(decodeURIComponent($page.params.name ?? ''));
  const adding = $derived(requested === 'new');
  const requestedModality = $derived(
    ($page.url.searchParams.get('modality') ?? undefined) as Modality | undefined
  );
  const blank = (): Axis => ({ name: '', modality: 'llm', label: '', describes: '', fields: [], missing: 'renormalise', min_coverage: .5, higher_is_better: true });
  let form = $state<Axis>(blank());
  let sources = $state<SourceRow[]>([]);
  let choices = $state<Record<string, SourceFieldRow[]>>({});
  let token = $state('');
  let loading = $state(true);
  let saving = $state(false);
  let message = $state('');
  let force = $state(false);
  // Delete used to fire straight off the button. An axis is the shape of a
  // ranking, so the profile page's two-step confirm is copied here.
  let confirming = $state(false);
  let removing = $state(false);

  $effect(() => {
    void Promise.all([adding ? Promise.resolve(null) : api.axis(requested, requestedModality), api.sources()]).then(([axis, found]) => {
      loading = false;
      if (axis && axis.ok) form = axis.value;
      else if (axis && !axis.ok) message = explainError(axis.error);
      if (found.ok) sources = found.value;
      for (const field of form.fields) void loadFields(field.source);
    });
  });

  async function loadFields(source: string) {
    if (!source || choices[source]) return;
    const found = await api.sourceFields(source);
    if (found.ok) choices = { ...choices, [source]: found.value };
  }
  function addField() {
    const source = sources[0]?.name ?? '';
    form.fields = [...form.fields, { source, field: '', weight: 1, phase: 1 }];
    void loadFields(source);
  }
  function removeField(index: number) { form.fields = form.fields.filter((_, i) => i !== index); }
  const total = $derived(form.fields.reduce((sum, field) => sum + Number(field.weight || 0), 0));

  async function save() {
    saving = true; message = '';
    const result = adding ? await api.createAxis(form, { token: token || undefined }) : await api.updateAxis(requested, form, { token: token || undefined });
    saving = false;
    if (!result.ok) { message = explainError(result.error); return; }
    message = 'Saved.';
    if (adding) {
      await goto(
        `/axes/${encodeURIComponent(form.name)}?modality=${encodeURIComponent(form.modality)}`
      );
    }
  }
  async function remove() {
    removing = true;
    const result = await api.removeAxis(requested, form.modality, force, { token: token || undefined });
    removing = false;
    confirming = false;
    if (!result.ok) { message = result.error.status === 409 ? `${result.error.message} — remove it there first, or force.` : explainError(result.error); return; }
    await goto('/axes');
  }
</script>

<svelte:head><title>{adding ? 'New axis' : form.label} · Sieve</title></svelte:head>
<a class="back" href="/axes">← Axes</a>
<h1>{adding ? 'New axis' : form.label}</h1>
{#if loading}<p>Loading…</p>{:else}
  <label class="token"><span>Token (profiles:write)</span><input type="password" bind:value={token} autocomplete="off" /></label>
  <div class="grid">
    <label><span>Name</span><input bind:value={form.name} disabled={!adding} pattern="[A-Za-z0-9_-]+" /></label>
    <label><span>Modality</span><select bind:value={form.modality} disabled={!adding}>{#each ['llm','text-to-image','image-editing','text-to-video','image-to-video','video-editing','text-to-speech','speech-to-text','speech-to-speech','music'] as modality}<option value={modality}>{modality}</option>{/each}</select></label>
    <label><span>Label</span><input bind:value={form.label} /></label>
    <label class="wide"><span>Describes</span><textarea bind:value={form.describes}></textarea></label>
    <label><span>Missing</span><select bind:value={form.missing}><option value="renormalise">renormalise</option><option value="penalise">penalise</option></select></label>
    <label><span>Minimum coverage</span><input type="number" min="0" max="1" step=".01" bind:value={form.min_coverage} /></label>
    <label class="check"><input type="checkbox" bind:checked={form.higher_is_better} /> Higher is better</label>
  </div>
  <div class="fields-head"><h2>Fields</h2><span>Weight sum: {total.toFixed(2)}</span><button type="button" onclick={addField}>Add row</button></div>
  <div class="fields">
    {#each form.fields as field, index (index)}
      <div class="field">
        <label><span>Source</span><select bind:value={field.source} onchange={() => loadFields(field.source)}>{#each sources as source}<option value={source.name}>{source.name}</option>{/each}</select></label>
        <label><span>Field</span><input list={`fields-${index}`} bind:value={field.field} /><datalist id={`fields-${index}`}>{#each choices[field.source] ?? [] as choice}<option value={choice.field}>{choice.rows} rows</option>{/each}</datalist></label>
        <label><span>Weight</span><input type="number" min=".000001" step=".01" bind:value={field.weight} /></label>
        <label><span>Phase</span><input type="number" min="1" step="1" bind:value={field.phase} /></label>
        <button class="remove" type="button" onclick={() => removeField(index)}>Remove</button>
      </div>
    {/each}
  </div>
  {#if message}<p class="message">{message}</p>{/if}
  <div class="actions"><button class="primary" type="button" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save'}</button>{#if !adding}<label class="force"><input type="checkbox" bind:checked={force} /> Force (set profile weights to 0)</label>{#if confirming}<span class="confirm">Delete {form.name || requested}?<button class="danger" type="button" disabled={removing} onclick={remove}>{removing ? 'Deleting…' : 'Yes, delete'}</button><button type="button" onclick={() => (confirming = false)}>Keep</button></span>{:else}<button class="danger" type="button" onclick={() => (confirming = true)}>Delete</button>{/if}{/if}</div>
{/if}

<style>
  .back{color:var(--muted);font-size:.8rem}h1{font-size:1.6rem;margin:.4rem 0 1rem}.token,.grid label,.field label{display:flex;flex-direction:column;gap:.2rem;font-size:.75rem;color:var(--muted)}.token{max-width:22rem;margin-bottom:1rem}.grid{display:grid;grid-template-columns:repeat(3,minmax(10rem,1fr));gap:.7rem}.wide{grid-column:span 2}.check{justify-content:end;flex-direction:row!important;align-items:center}input,select,textarea,button{font:inherit;color:var(--ink);background:var(--panel2);border:1px solid var(--rule);border-radius:7px;padding:.4rem .5rem}textarea{min-height:4.5rem}.fields-head{display:flex;gap:1rem;align-items:center;margin-top:1.4rem}.fields-head h2{margin:0}.fields-head span{color:var(--muted);margin-right:auto}.field{display:grid;grid-template-columns:1fr 2fr 7rem 6rem auto;gap:.6rem;align-items:end;padding:.65rem 0;border-bottom:1px solid var(--rule)}.remove,.danger{border-color:var(--bad);color:var(--bad)}.actions{display:flex;gap:.8rem;align-items:center;margin-top:1rem}.confirm{display:inline-flex;align-items:center;gap:.35rem;font-size:.74rem;color:var(--muted)}.primary{border-color:var(--accent)}.force{color:var(--muted);font-size:.75rem;margin-left:auto}.message{color:var(--accent)}@media(max-width:800px){.grid,.field{grid-template-columns:1fr}.wide{grid-column:auto}}
</style>
