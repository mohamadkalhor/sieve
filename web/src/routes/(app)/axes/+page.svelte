<script lang="ts">
  import { api, explainError, type ApiError, type AxisRow } from '$lib/api/client';

  let axes = $state<AxisRow[]>([]);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);

  $effect(() => {
    void api.axes().then((result) => {
      loading = false;
      if (result.ok) axes = result.value;
      else error = result.error;
    });
  });

  const groups = $derived.by(() => {
    const found = new Map<string, AxisRow[]>();
    for (const axis of axes) found.set(axis.modality, [...(found.get(axis.modality) ?? []), axis]);
    return [...found.entries()].sort(([a], [b]) => a.localeCompare(b));
  });
</script>

<svelte:head><title>Axes · Sieve</title></svelte:head>
<header>
  <div><h1>Axes</h1><p>Criteria assembled from measured benchmark columns. Profiles weight these names.</p></div>
  <a class="new" href="/axes/new">New axis</a>
</header>
{#if loading}<p class="muted">Loading…</p>{/if}
{#if error}<p class="error">{explainError(error)}</p>{/if}
{#each groups as [modality, rows] (modality)}
  <section>
    <h2>{modality} <span>{rows.length}</span></h2>
    <div class="table">
      {#each rows as axis (axis.name)}
        <a class="row" href={`/axes/${encodeURIComponent(axis.name)}?modality=${encodeURIComponent(axis.modality)}`}>
          <strong>{axis.label}</strong><code>{axis.name}</code><p>{axis.describes}</p>
          <small>{axis.fields_count} fields · {axis.profiles.length ? `used by ${axis.profiles.join(', ')}` : 'unused'}</small>
          {#if axis.builtin}<b class="chip">builtin</b>{/if}
        </a>
      {/each}
    </div>
  </section>
{/each}

<style>
  header{display:flex;justify-content:space-between;gap:1rem;align-items:start} h1{margin:0;font-size:1.6rem} header p{color:var(--muted);margin:.25rem 0 1rem}.new{border:1px solid var(--accent);border-radius:7px;padding:.35rem .75rem;background:var(--panel2)}h2{font-size:1rem;margin:1.3rem 0 .45rem;text-transform:uppercase;letter-spacing:.06em}h2 span{color:var(--muted);font-weight:normal}.table{border:1px solid var(--rule);border-radius:var(--radius);overflow:hidden}.row{display:grid;grid-template-columns:12rem 12rem 1fr auto auto;gap:.75rem;align-items:center;padding:.7rem .85rem;border-bottom:1px solid var(--rule);color:var(--ink)}.row:last-child{border:0}.row:hover{background:var(--panel2)}code,small,.row p{color:var(--muted);margin:0}.chip{font-size:.68rem;border:1px solid var(--rule);border-radius:1rem;padding:.1rem .45rem;color:var(--muted)}.error{color:var(--bad)}@media(max-width:900px){.row{grid-template-columns:1fr auto}.row p,.row small{grid-column:1/-1}}
</style>
