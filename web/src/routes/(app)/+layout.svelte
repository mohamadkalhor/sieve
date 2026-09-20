<script lang="ts">
  /**
   * The shell every screen sits in: the top bar, the stage, the status bar
   * (CONSOLE.md section 6.1).
   *
   * The rail is gone. Its sections are in the top bar, the two readings it
   * used to make about the server live where they belong -- "API unreachable"
   * and the loop's last pull in the status bar -- and what is left here is the
   * one thing every screen needs: who is signed in, asked once.
   *
   * The stores are created here, once, during initialisation, because context
   * only travels downwards from where it is set: a store made inside an
   * `$effect` would be too late for every child that reads it.
   */
  import type { Snippet } from 'svelte';
  import { session } from '$lib/session.svelte';
  import Shell from '$lib/console/shell/Shell.svelte';
  import { shellStores } from '$lib/console/shell/stores.svelte';
  import { providePalette, provideSeats, provideStatus } from '$lib/console/context';

  let { children }: { children?: Snippet } = $props();

  const stores = shellStores();
  provideSeats(stores.seats);
  provideStatus(stores.status);
  providePalette(stores.palette);

  $effect(() => {
    void session.refresh();
  });

  // The status bar's data has to keep arriving, so the store polls -- and this
  // is the effect that says for how long, since the store works outside a
  // component on purpose. Its answers are also what signs a person in on a
  // server whose `/v1/me` never landed (see `StatusStore`).
  $effect(() => stores.status.start());
</script>

<Shell>{@render children?.()}</Shell>
