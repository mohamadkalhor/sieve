<script lang="ts">
  /**
   * Connectors: the gateways Sieve reads models from, and pushes routing to.
   *
   * This setup used to be a block in a file on the server, so anyone whose
   * router was not the one in the example could not add it without a shell on
   * the box. Every field here is that same data, editable from the page.
   *
   * Nothing on this screen ever shows a key. `token_env` is the *name* of an
   * environment variable; the value it holds stays on the server and is never
   * part of a response.
   */
  import {
    api,
    explainError,
    type ApiError,
    type ConnectorBody,
    type ConnectorKind,
    type ConnectorRow
  } from '$lib/api/client';
  import Chip from '$lib/components/Chip.svelte';
  import Empty from '$lib/components/Empty.svelte';

  const KINDS: { value: ConnectorKind; label: string }[] = [
    { value: 'ninerouter', label: '9router' },
    { value: 'openai_compat', label: 'OpenAI-compatible' }
  ];

  const kindLabel = (kind: string) => KINDS.find((k) => k.value === kind)?.label ?? kind;

  const blank = (): ConnectorBody => ({
    name: '',
    kind: 'ninerouter',
    base_url: '',
    token_env: '',
    read: true,
    write: false,
    poll_minutes: 60
  });

  let rows = $state<ConnectorRow[]>([]);
  let error = $state<ApiError | null>(null);
  let loading = $state(true);
  /**
   * The route is not on this server yet (AMS-20 lands it). One banner says so
   * and Add is disabled -- rather than an Add button that 404s on submit.
   */
  let absent = $state(false);
  let token = $state('');
  let notice = $state('');

  let formOpen = $state(false);
  /** null while adding; the id of the row being edited otherwise */
  let editingId = $state<string | null>(null);
  let form = $state<ConnectorBody>(blank());
  let formError = $state('');
  let saving = $state(false);

  /** per row: the action in flight, and the last thing a probe or a save said */
  let busy = $state<Record<string, string>>({});
  let said = $state<Record<string, { ok: boolean; text: string }>>({});
  let confirming = $state<string | null>(null);

  const options = $derived({ token: token || undefined });

  async function load() {
    const result = await api.connectors();
    loading = false;

    /*
      The route not being there does not always arrive as a 404. FastAPI mounts
      the built web app at `/`, so a path it does not recognise -- one under
      `/v1` included -- is answered with the SPA shell: 200, text/html, which
      the client hands back as a null body. A server without the module was
      measured doing exactly that, and `rows.length` on a null is a blank
      screen rather than a banner. Both shapes mean the same thing.
    */
    const missing =
      (!result.ok && (result.error.status === 404 || result.error.code === 'not_built')) ||
      (result.ok && !Array.isArray(result.value));

    if (missing) {
      absent = true;
      rows = [];
      error = null;
      return;
    }
    if (!result.ok) {
      error = result.error;
      return;
    }
    rows = result.value;
    absent = false;
    error = null;
  }

  $effect(() => {
    void load();
  });

  function urlOk(value: string): boolean {
    try {
      const parsed = new URL(value.trim());
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  }

  function openAdd() {
    form = blank();
    editingId = null;
    formError = '';
    formOpen = true;
  }

  function openEdit(row: ConnectorRow) {
    form = {
      name: row.name,
      kind: row.kind,
      base_url: row.base_url,
      token_env: row.token_env,
      read: row.read,
      write: row.write,
      poll_minutes: row.poll_minutes
    };
    editingId = row.id;
    formError = '';
    formOpen = true;
  }

  function closeForm() {
    formOpen = false;
    editingId = null;
    formError = '';
  }

  async function submit() {
    formError = '';
    if (!form.name.trim()) {
      formError = 'Give it a name, so the row can be told from the others.';
      return;
    }
    if (!urlOk(form.base_url)) {
      formError = 'The base URL has to be a http:// or https:// address.';
      return;
    }
    if (!Number.isFinite(form.poll_minutes) || form.poll_minutes < 1) {
      formError = 'Poll every one minute or more.';
      return;
    }

    const body: ConnectorBody = {
      ...form,
      name: form.name.trim(),
      base_url: form.base_url.trim().replace(/\/$/, ''),
      token_env: form.token_env.trim(),
      poll_minutes: Number(form.poll_minutes)
    };

    saving = true;
    const result = editingId
      ? await api.updateConnector(editingId, body, options)
      : await api.createConnector(body, options);
    saving = false;

    if (!result.ok) {
      formError = explainError(result.error);
      return;
    }
    notice = editingId ? `${body.name} saved.` : `${body.name} added.`;
    closeForm();
    await load();
  }

  /**
   * The switches write through: there is no Save on a row, because a switch
   * that looks thrown and is not saved is a lie about what the router is
   * doing. If the PUT fails the box goes back to where it was and says why.
   */
  async function setFlag(
    row: ConnectorRow,
    field: 'read' | 'write',
    value: boolean,
    box: HTMLInputElement
  ) {
    busy = { ...busy, [row.id]: field };
    said = { ...said, [row.id]: { ok: true, text: '' } };
    const result = await api.updateConnector(row.id, { [field]: value }, options);
    busy = { ...busy, [row.id]: '' };
    if (result.ok) {
      rows = rows.map((r) => (r.id === row.id ? result.value : r));
      said = { ...said, [row.id]: { ok: true, text: `${field} ${value ? 'on' : 'off'}` } };
      return;
    }
    box.checked = row[field];
    said = { ...said, [row.id]: { ok: false, text: explainError(result.error) } };
  }

  async function probe(row: ConnectorRow, what: 'test' | 'pull') {
    busy = { ...busy, [row.id]: what };
    said = { ...said, [row.id]: { ok: true, text: '' } };
    const result =
      what === 'test'
        ? await api.testConnector(row.id, options)
        : await api.pullConnector(row.id, options);
    busy = { ...busy, [row.id]: '' };

    if (!result.ok) {
      said = { ...said, [row.id]: { ok: false, text: explainError(result.error) } };
      return;
    }
    const value = result.value;
    said = {
      ...said,
      [row.id]: value.ok
        ? {
            ok: true,
            text: `ok · ${value.models_count} model${value.models_count === 1 ? '' : 's'}`
          }
        : { ok: false, text: value.error || 'it answered, but not with models' }
    };
    if (what === 'pull') await load();
  }

  async function remove(row: ConnectorRow) {
    busy = { ...busy, [row.id]: 'remove' };
    const result = await api.removeConnector(row.id, options);
    busy = { ...busy, [row.id]: '' };
    confirming = null;
    if (!result.ok) {
      said = { ...said, [row.id]: { ok: false, text: explainError(result.error) } };
      return;
    }
    notice = `${row.name} removed.`;
    await load();
  }

  const when = (value: string | null) =>
    value
      ? new Date(value).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
      : 'never';

  /* ---------------------------------------------------------------------- */
  /* the default cost multipliers                                            */
  /* ---------------------------------------------------------------------- */

  /**
   * What an id from one gateway really costs you, against its published price.
   *
   * It belongs here because a prefix *is* a connector: the local ids a gateway
   * serves are `oc-go/glm-5.3`, and the number in front of the slash is the
   * thing being paid for. Every profile inherits these; a seat that pays
   * differently overrides one on its own page.
   *
   * The prefixes are Sieve's own -- it derives them from the inventory it has
   * seen -- so this screen offers the ones that come back and never invents
   * one.
   */
  let multipliers = $state<Record<string, number> | null>(null);
  let multipliersLoading = $state(true);
  let multiplierSaid = $state<Record<string, { ok: boolean; text: string }>>({});
  let savingPrefix = $state('');

  $effect(() => {
    void (async () => {
      const result = await api.costMultipliers();
      multipliersLoading = false;
      // Same absence as everywhere else on this screen: a route that is not
      // mounted comes back as the SPA shell, which is a 200 with a null body.
      multipliers =
        result.ok && result.value && typeof result.value === 'object' && !Array.isArray(result.value)
          ? result.value
          : null;
    })();
  });

  const prefixes = $derived(Object.keys(multipliers ?? {}).sort());

  async function setMultiplier(prefix: string, raw: string, box: HTMLInputElement) {
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      multiplierSaid = {
        ...multiplierSaid,
        [prefix]: { ok: false, text: 'a multiplier is a number, and never below zero' }
      };
      box.value = String(multipliers?.[prefix] ?? 1);
      return;
    }
    savingPrefix = prefix;
    // Merged by the server, so only the prefix that changed is sent.
    const result = await api.saveCostMultipliers({ [prefix]: value }, options);
    savingPrefix = '';
    if (!result.ok) {
      multiplierSaid = {
        ...multiplierSaid,
        [prefix]: { ok: false, text: explainError(result.error) }
      };
      box.value = String(multipliers?.[prefix] ?? 1);
      return;
    }
    multipliers =
      result.value && typeof result.value === 'object'
        ? result.value
        : { ...(multipliers ?? {}), [prefix]: value };
    multiplierSaid = { ...multiplierSaid, [prefix]: { ok: true, text: 'saved' } };
  }
</script>

{#snippet formCard()}
  <form
    class="form"
    onsubmit={(e) => {
      e.preventDefault();
      void submit();
    }}
  >
    <h3>{editingId ? 'Edit connector' : 'Add a connector'}</h3>

    <div class="fields">
      <label>
        <span>Name</span>
        <input bind:value={form.name} placeholder="my router" autocomplete="off" />
      </label>

      <label>
        <span>Kind</span>
        <select bind:value={form.kind}>
          {#each KINDS as kind (kind.value)}
            <option value={kind.value}>{kind.label}</option>
          {/each}
        </select>
      </label>

      <label class="wide">
        <span>Base URL</span>
        <input
          class="mono"
          bind:value={form.base_url}
          placeholder="https://router.example.com/v1"
          autocomplete="off"
          inputmode="url"
        />
      </label>

      <label class="wide">
        <span>Token env</span>
        <input class="mono" bind:value={form.token_env} placeholder="MY_ROUTER_KEY" autocomplete="off" />
        <small>name of the environment variable that holds the key</small>
      </label>

      <label>
        <span>Poll every (minutes)</span>
        <input type="number" min="1" step="1" bind:value={form.poll_minutes} />
      </label>

      <div class="switches">
        <label class="check">
          <input type="checkbox" bind:checked={form.read} />
          <span>Read <small>pull the model list from it</small></span>
        </label>
        <label class="check">
          <input type="checkbox" bind:checked={form.write} />
          <span>Write <small>push routing decisions to it</small></span>
        </label>
      </div>
    </div>

    {#if formError}<p class="error">{formError}</p>{/if}

    <div class="form-actions">
      <button type="submit" class="primary" disabled={saving}>
        {saving ? 'Saving…' : editingId ? 'Save' : 'Add connector'}
      </button>
      <button type="button" onclick={closeForm} disabled={saving}>Cancel</button>
    </div>
  </form>
{/snippet}

<svelte:head><title>Connectors · Sieve</title></svelte:head>

<h1>Connectors</h1>
<p class="lede">
  The gateways Sieve reads models from, and writes routing back to. Keys are never held here —
  only the name of the environment variable that holds one.
</p>

<label class="token">
  <span>Token (needed to change anything)</span>
  <input
    type="password"
    bind:value={token}
    placeholder="a token with apply and profiles:write"
    autocomplete="off"
  />
</label>

{#if absent}
  <p class="banner" role="status">Connectors API not available on this server yet.</p>
{/if}
{#if notice}<p class="notice">{notice}</p>{/if}
{#if error}<p class="error">{explainError(error)}</p>{/if}

<div class="toolbar">
  <button type="button" class="primary" onclick={openAdd} disabled={absent || (formOpen && !editingId)}>
    Add connector
  </button>
</div>

{#if formOpen && editingId === null}
  {@render formCard()}
{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if rows.length === 0}
  <Empty
    error={null}
    title={absent ? 'Nothing to configure yet' : 'No connectors yet'}
    hint={'A connector is a gateway Sieve talks to: it reads the model list from one, and can push the routing it decides back to it. ' +
      'Add one with its base URL and the name of the environment variable that holds its key — the key itself never leaves the server.'}
  />
{:else}
  <ul class="rows">
    {#each rows as row (row.id)}
      {@const last = said[row.id]}
      <li class="row" class:off={!row.read && !row.write}>
        <div class="head">
          <span class="name">{row.name}</span>
          <Chip label="kind" value={kindLabel(row.kind)} />
          <span class="url mono" title={row.base_url}>{row.base_url}</span>
          <span class="env mono" title="name of the environment variable that holds the key">
            {row.token_env || '—'}
          </span>
        </div>

        <div class="flags">
          <label class="switch">
            <input
              type="checkbox"
              checked={row.read}
              disabled={busy[row.id] === 'read'}
              onchange={(e) => void setFlag(row, 'read', e.currentTarget.checked, e.currentTarget)}
            />
            <span>read</span>
          </label>
          <label class="switch">
            <input
              type="checkbox"
              checked={row.write}
              disabled={busy[row.id] === 'write'}
              onchange={(e) => void setFlag(row, 'write', e.currentTarget.checked, e.currentTarget)}
            />
            <span>write</span>
          </label>
          {#if busy[row.id] === 'read' || busy[row.id] === 'write'}
            <span class="saving">saving…</span>
          {/if}
          <span class="when">
            pulled {when(row.last_pull_at)} · pushed {when(row.last_push_at)} · every {row.poll_minutes}
            min
          </span>
        </div>

        {#if row.last_error}
          <p class="error one-line" title={row.last_error}>{row.last_error}</p>
        {/if}
        {#if last && last.text}
          <p class={last.ok ? 'notice one-line' : 'error one-line'} title={last.text}>{last.text}</p>
        {/if}

        <div class="actions">
          <button type="button" onclick={() => void probe(row, 'test')} disabled={!!busy[row.id]}>
            {busy[row.id] === 'test' ? 'testing…' : 'Test'}
          </button>
          <button type="button" onclick={() => void probe(row, 'pull')} disabled={!!busy[row.id]}>
            {busy[row.id] === 'pull' ? 'pulling…' : 'Pull now'}
          </button>
          <button type="button" onclick={() => openEdit(row)} disabled={!!busy[row.id]}>Edit</button>
          {#if confirming === row.id}
            <span class="confirm">
              Remove {row.name}?
              <button type="button" class="danger" onclick={() => void remove(row)} disabled={!!busy[row.id]}>
                {busy[row.id] === 'remove' ? 'removing…' : 'Yes, remove'}
              </button>
              <button type="button" onclick={() => (confirming = null)}>Keep</button>
            </span>
          {:else}
            <button type="button" onclick={() => (confirming = row.id)} disabled={!!busy[row.id]}>
              Remove
            </button>
          {/if}
        </div>

        {#if formOpen && editingId === row.id}
          {@render formCard()}
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<section class="defaults">
  <h2>Default cost multipliers</h2>
  <p class="lede">
    Multiplies the list price for every id with this prefix; 0.1 means a flat-rate subscription you
    barely pay for. Every profile inherits these, and a seat that pays differently overrides one on
    its own page.
  </p>

  {#if multipliersLoading}
    <p class="muted">Loading…</p>
  {:else if multipliers === null}
    <p class="banner" role="status">Cost multipliers API not available on this server yet.</p>
  {:else if prefixes.length === 0}
    <p class="muted">
      No prefixes yet. Sieve reads them from the local ids your connectors serve — pull one and they
      appear here.
    </p>
  {:else}
    <ul class="prefixes">
      {#each prefixes as prefix (prefix)}
        {@const last = multiplierSaid[prefix]}
        <li>
          <label for={`mult-${prefix}`} class="mono">{prefix}</label>
          <input
            id={`mult-${prefix}`}
            type="number"
            min="0"
            step="0.05"
            value={multipliers?.[prefix] ?? 1}
            disabled={savingPrefix === prefix}
            onchange={(e) => void setMultiplier(prefix, e.currentTarget.value, e.currentTarget)}
          />
          <span class={last && !last.ok ? 'error one-line' : 'note'}>
            {savingPrefix === prefix ? 'saving…' : last ? last.text : 'default 1.0'}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .defaults {
    margin-top: 1.6rem;
    border-top: 1px solid var(--rule);
    padding-top: 0.9rem;
  }
  .defaults h2 {
    font-family: var(--ui);
    font-size: 1rem;
    margin: 0;
  }
  .prefixes {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    gap: 0.3rem 0.8rem;
  }
  .prefixes li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 6rem minmax(0, 8rem);
    gap: 0.5rem;
    align-items: center;
    padding: 0.22rem 0;
    border-bottom: 1px solid var(--rule);
    font-size: 0.8rem;
  }
  .prefixes input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 6px;
    color: var(--ink);
    font: inherit;
    font-size: 0.8rem;
    padding: 0.2rem 0.35rem;
    min-width: 0;
  }
  .note {
    color: var(--muted);
    font-size: 0.7rem;
  }
  h1 {
    font-size: 1.6rem;
    margin: 0;
  }
  .lede {
    color: var(--muted);
    margin: 0.25rem 0 1rem;
    max-width: 62ch;
  }
  .token {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.78rem;
    color: var(--muted);
    max-width: 22rem;
    margin-bottom: 0.8rem;
  }
  .token input {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
  }
  .banner {
    border: 1px solid var(--rule);
    border-left: 2px solid var(--accent);
    border-radius: 7px;
    background: var(--panel);
    color: var(--muted);
    padding: 0.5rem 0.7rem;
    font-size: 0.82rem;
    margin: 0 0 0.8rem;
  }
  .toolbar {
    margin-bottom: 0.8rem;
  }

  /* ---- the list ---- */
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .row {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.7rem 0.85rem;
  }
  .row.off {
    opacity: 0.6;
  }
  .head {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.5rem;
    min-width: 0;
  }
  .name {
    font-size: 0.95rem;
    color: var(--ink);
  }
  .url {
    font-size: 0.76rem;
    color: var(--reach);
    max-width: 28rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .env {
    font-size: 0.72rem;
    color: var(--muted);
  }
  .flags {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-top: 0.45rem;
  }
  .switch {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.76rem;
    color: var(--muted);
    cursor: pointer;
  }
  .saving {
    font-size: 0.72rem;
    color: var(--accent);
  }
  .when {
    color: var(--muted);
    font-size: 0.72rem;
    margin-left: auto;
  }
  .actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin-top: 0.5rem;
  }
  .confirm {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.76rem;
    color: var(--muted);
  }

  /* ---- the form ---- */
  .form {
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.85rem 0.9rem;
    margin: 0.6rem 0 1rem;
  }
  .form h3 {
    font-family: var(--display);
    font-weight: 300;
    font-size: 1rem;
    margin: 0 0 0.6rem;
  }
  .fields {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 0.6rem 0.8rem;
  }
  .fields .wide {
    grid-column: span 2;
  }
  .fields label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.78rem;
    color: var(--muted);
    min-width: 0;
  }
  .fields input:not([type='checkbox']),
  .fields select {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--ink);
    padding: 0.3rem 0.5rem;
    font: inherit;
    min-width: 0;
  }
  .fields small {
    color: var(--muted);
    font-size: 0.7rem;
  }
  .switches {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    justify-content: center;
  }
  .check {
    flex-direction: row !important;
    align-items: baseline;
    gap: 0.4rem;
  }
  .check span {
    color: var(--ink);
    font-size: 0.8rem;
  }
  .check small {
    display: block;
  }
  .form-actions {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.7rem;
  }

  /* ---- shared ---- */
  button {
    background: var(--panel2);
    border: 1px solid var(--rule);
    border-radius: 7px;
    color: var(--muted);
    font: inherit;
    font-size: 0.72rem;
    padding: 0.15rem 0.55rem;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    color: var(--ink);
    border-color: var(--accent);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button.primary {
    color: var(--ink);
    border-color: var(--accent);
  }
  button.danger:hover:not(:disabled) {
    color: var(--bad);
    border-color: var(--bad);
  }
  .muted {
    color: var(--muted);
  }
  .notice {
    color: var(--good);
    font-size: 0.78rem;
    margin: 0.35rem 0 0;
  }
  .error {
    color: var(--bad);
    font-size: 0.78rem;
    margin: 0.35rem 0 0;
    overflow-wrap: anywhere;
  }
  .one-line {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow-wrap: normal;
  }
  @media (max-width: 700px) {
    .fields .wide {
      grid-column: span 1;
    }
    .when {
      margin-left: 0;
    }
  }
</style>
