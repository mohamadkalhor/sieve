<script lang="ts">
  import { api, type SourceRow } from '$lib/api/client';
  import Rail from '$lib/components/Rail.svelte';

  let { children } = $props();

  let sources = $state<SourceRow[]>([]);

  $effect(() => {
    let alive = true;
    api.sources().then((result) => {
      if (alive && result.ok) sources = result.value;
    });
    return () => {
      alive = false;
    };
  });

  const lastPull = $derived(
    sources.map((s) => s.last_pull).filter(Boolean).sort().at(-1) ?? null
  );
</script>

<div class="shell">
  <Rail {lastPull} sources={sources.filter((s) => s.enabled).length} />
  <main>{@render children()}</main>
</div>

<style>
  .shell {
    display: flex;
    min-height: 100dvh;
    align-items: stretch;
  }
  main {
    flex: 1;
    min-width: 0;
    padding: 1.5rem 1.75rem 4rem;
  }
  @media (max-width: 700px) {
    .shell {
      flex-direction: column;
    }
    main {
      padding: 1rem 0.9rem 3rem;
    }
  }
</style>
