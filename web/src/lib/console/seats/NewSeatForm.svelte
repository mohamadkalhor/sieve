<script lang="ts">
  /**
   * The create form (CONSOLE.md section 6.2), ported from
   * `routes/(app)/profiles/+page.svelte`.
   *
   * A name, a modality, and optionally a seat to copy. Copying is limited to
   * the same modality because a profile is weights over axes that exist for one
   * modality and nothing else: a list offering every seat is a list of mostly
   * wrong answers, and the server would refuse most of them.
   *
   * The seat list it offers is the one already loaded -- the pane's own rows --
   * so opening this form is not a request.
   */
  import { api, explainError, MODALITY_OPTIONS } from '$lib/api/client';
  import type { Modality } from '$lib/types';
  import { session } from '$lib/session.svelte';
  import { seats as seatsStore } from '../context';
  import { MODALITY_LABEL } from '../logic/seats';

  interface Props {
    /** the new seat's name, once the server has it */
    oncreated: (name: string) => void;
  }

  let { oncreated }: Props = $props();

  const store = seatsStore();
  /** one token for the whole app, and none at all when gate signed you in */
  const token = $derived(session.token);

  let name = $state('');
  let modality = $state<Modality>('llm');
  let copyFrom = $state('');
  let creating = $state(false);
  let error = $state('');

  const copyable = $derived((store.rows ?? []).filter((row) => row.modality === modality));

  // changing the modality drops a choice that no longer belongs to it
  $effect(() => {
    if (copyFrom && !copyable.some((row) => row.name === copyFrom)) copyFrom = '';
  });

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const wanted = name.trim();
    if (!wanted) {
      error = 'Give it a name.';
      return;
    }
    creating = true;
    error = '';
    const options = { token: token || undefined };

    let result = await api.newProfile(
      {
        name: wanted,
        modality,
        from: copyFrom || undefined,
        copy_from: copyFrom || undefined
      },
      options
    );
    if (!result.ok && copyFrom && result.error.status === 400) {
      // a server from before this shape clones by `from` alone
      result = await api.createProfile({ name: wanted, from: copyFrom }, options);
    }
    creating = false;

    if (!result.ok) {
      error = explainError(result.error);
      return;
    }
    name = '';
    copyFrom = '';
    oncreated(wanted);
  }
</script>

<form class="form" onsubmit={submit}>
  <label class="field">
    <span>Name</span>
    <input bind:value={name} placeholder="coder" />
  </label>

  <label class="field">
    <span>Modality</span>
    <select bind:value={modality}>
      {#each MODALITY_OPTIONS as option (option)}
        <option value={option}>{MODALITY_LABEL[option]}</option>
      {/each}
    </select>
  </label>

  <label class="field">
    <span>Copy from</span>
    <select bind:value={copyFrom}>
      <option value="">nothing — start empty</option>
      {#each copyable as row (row.name)}
        <option value={row.name}>{row.name}</option>
      {/each}
    </select>
  </label>

  {#if error}<p class="err" role="alert">{error}</p>{/if}

  <button class="go" type="submit" disabled={creating}>
    {creating ? 'Creating…' : 'Create seat'}
  </button>
</form>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 4px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .field > span {
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--c-muted);
  }
  input,
  select {
    height: 28px;
    padding: 0 6px;
    background: var(--c-raised);
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-2);
    color: var(--c-ink);
    font-family: var(--f-ui);
    font-size: 13px;
  }
  input:focus-visible,
  select:focus-visible {
    outline: 2px solid var(--c-accent);
    outline-offset: 1px;
  }
  .err {
    margin: 0;
    color: var(--c-bad);
    font-size: 12px;
  }
  .go {
    height: 30px;
    border: 1px solid var(--c-accent);
    border-radius: var(--r-2);
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font-family: var(--f-ui);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .go[disabled] {
    opacity: 0.6;
    cursor: default;
  }
</style>
