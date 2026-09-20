<script lang="ts">
  /**
   * An id a router reports that no catalogue entry matches yet (CONSOLE.md
   * section 6.6).
   *
   * It is deliberately not a `ModelRow`: there is no score, no cost and no
   * capabilities, because there is no model -- only a name traffic has arrived
   * under. The one button makes one, and says so while it is happening.
   */
  import Button from '../ui/Button.svelte';

  interface Props {
    local_id: string;
    name: string;
    /** what the button does once the id has a model: the seat's own verb */
    action: 'pin' | 'add';
    busy?: boolean;
    onlink: () => void;
  }

  let { local_id, name, action, busy = false, onlink }: Props = $props();

  const label = $derived(action === 'pin' ? `Pin ${name}` : `Add ${name}`);
</script>

<div class="row" role="row" data-local-id={local_id}>
  <div class="cell at num" role="cell">—</div>

  <div class="cell model" role="cell">
    <span class="name">{name}</span>
    <span class="id">{local_id}</span>
    <span class="tag" title="Traffic arrived under this id but no model has been made of it">
      unknown
    </span>
  </div>

  <div class="cell acts" role="cell">
    <Button variant="outline" size="sm" disabled={busy} onclick={onlink}>
      {busy ? 'Linking…' : label}
    </Button>
  </div>
</div>

<style>
  .row {
    display: grid;
    grid-template-columns: var(--cols, 24px 168px minmax(56px, 1fr) 66px 104px 46px 56px);
    align-items: center;
    gap: 10px;
    height: 40px;
    padding: 0 12px;
    border-bottom: 1px solid var(--c-rule-soft);
    opacity: 0.72;
  }

  .cell {
    display: flex;
    align-items: center;
    min-width: 0;
  }

  .at {
    color: var(--c-muted);
  }

  .model {
    gap: 8px;
  }

  .name {
    font-family: var(--f-ui);
    font-size: 13px;
    color: var(--c-ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .id {
    font-family: var(--f-mono);
    font-size: 11px;
    color: var(--c-muted);
    white-space: nowrap;
  }

  .tag {
    padding: 0 5px;
    border: 1px solid var(--c-rule-strong);
    border-radius: var(--r-pill);
    color: var(--c-muted);
    font-family: var(--f-ui);
    font-size: 10px;
  }

  /* the button is the row's only control, so it takes the columns after the name */
  .acts {
    grid-column: 3 / -1;
    justify-content: flex-end;
  }

  @media (max-width: 899px) {
    .row {
      grid-template-columns: 24px minmax(0, 1fr) auto;
    }
  }
</style>
