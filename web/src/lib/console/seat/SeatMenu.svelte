<script lang="ts">
  /**
   * The seat's own menu (CONSOLE.md section 6.3): the four things that happen
   * to a seat rather than inside it.
   *
   * Deleting and renaming are worth a confirmation because they change the URL
   * you are standing on, so this menu only opens the question -- `AskBar` asks
   * it.
   */
  import type { SeatSession } from '$lib/console/state/seat.svelte';
  import Icon from '$lib/console/ui/Icon.svelte';
  import IconButton from '$lib/console/ui/IconButton.svelte';
  import Popover from '$lib/console/ui/Popover.svelte';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  let anchor = $state<HTMLElement | null>(null);
  let open = $state(false);

  function close(): void {
    open = false;
  }
</script>

<IconButton
  label="More"
  pressed={open}
  onclick={(event) => {
    anchor = event.currentTarget;
    open = !open;
  }}
>
  <Icon name="dots" size={14} />
</IconButton>

<Popover {open} {anchor} onclose={close} label="Seat menu" width={170}>
  <ul class="items" role="menu">
    <li>
      <button
        type="button"
        role="menuitem"
        onclick={() => {
          session.askAbout('copy');
          close();
        }}>Copy</button
      >
    </li>
    <li>
      <button
        type="button"
        role="menuitem"
        onclick={() => {
          session.askAbout('rename');
          close();
        }}>Rename</button
      >
    </li>
    <li>
      <button
        type="button"
        role="menuitem"
        onclick={() => {
          session.askAbout('delete');
          close();
        }}>Delete</button
      >
    </li>
    <li>
      <button
        type="button"
        role="menuitem"
        onclick={() => {
          close();
          void session.openHistory();
        }}>History</button
      >
    </li>
  </ul>
</Popover>

<style>
  .items {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
  }

  .items button {
    display: block;
    width: 100%;
    padding: 7px 10px;
    border: 0;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--c-ink-2);
    font-family: inherit;
    font-size: 12px;
    text-align: left;
    cursor: pointer;
  }

  .items button:hover {
    background: var(--c-raised);
    color: var(--c-ink);
  }
</style>
