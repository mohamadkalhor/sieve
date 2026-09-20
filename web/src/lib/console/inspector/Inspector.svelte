<script lang="ts">
  /**
   * The inspector (CONSOLE.md section 6.7).
   *
   * Six blocks about one model: where it stands, where its score came from,
   * what it can do and who says so, what it costs, who serves it, and the two
   * things you can do about it. What each block draws is decided in `view.ts`,
   * so this file is only about assembling them and about the two states that
   * are not a drawing at all -- nothing selected, and the card still arriving.
   *
   * The card itself is read once per model and kept (`state/card.svelte.ts`),
   * which is why opening a model twice costs one request and why a failed read
   * can be asked again without losing what is already on screen.
   */
  import type { Modality } from '$lib/types';
  import { explainError } from '$lib/api/client';
  import { selection } from '../context';
  import { cardCache, type CardRead } from '../state/card.svelte';
  import type { SeatSession } from '../state/seat.svelte';
  import Button from '../ui/Button.svelte';
  import Skeleton from '../ui/Skeleton.svelte';
  import AbilityTable from './AbilityTable.svelte';
  import InspectorActions from './InspectorActions.svelte';
  import PriceBlock from './PriceBlock.svelte';
  import ServedBy from './ServedBy.svelte';
  import WhyBars from './WhyBars.svelte';
  import {
    abilityRows,
    capsText,
    inStep,
    NO_ROW_SERVED,
    NO_ROW_YET,
    NO_SELECTION,
    priceFigures,
    standingOf,
    waiting
  } from './view';

  interface Props {
    session: SeatSession;
  }

  let { session }: Props = $props();

  const pick = selection();
  const cache = cardCache();

  const id = $derived(pick.id);
  /** The seat's modality: a card is about a model *in* a modality. */
  const modality = $derived<Modality | null>(session.profile?.modality ?? null);
  const standing = $derived(standingOf(id ?? '', session.preview));

  /** A read that has not started draws what a read in flight draws. */
  const IDLE: CardRead = {
    card: null,
    state: 'loading',
    error: null,
    priceError: null,
    retry: () => {}
  };

  const read = $derived.by((): CardRead => {
    if (!id || !modality) return IDLE;
    return cache.get(id, modality, standing.row);
  });

  const settings = $derived(session.settings);
  const agree = $derived(inStep(session.diff));
  const leader = $derived(session.preview?.models?.[0] ?? null);
  const abilities = $derived(abilityRows(read.card, standing.row));
  const figures = $derived(
    read.card ? priceFigures(read.card.price, standing.row, session.name) : []
  );
  /**
   * The card read failed and there is no earlier one to fall back on: the price
   * is unknown, which is not the same as "nobody posted one".
   */
  const priceUnknown = $derived(read.state === 'error' && read.card === null);
  /** Whether the id list is an answer at all, or only our own ignorance. */
  const servedKnown = $derived(
    read.card !== null && (read.state !== 'fallback' || standing.row !== null)
  );
</script>

{#if !id}
  <div class="root" data-block="none">
    <p class="quiet">{NO_SELECTION}</p>
  </div>
{:else}
  <div class="root" data-block="inspector">
    <header class="head">
      <p class="caps">{capsText(standing, agree)}</p>
      <h2>{standing.name}</h2>
      {#if standing.row?.local_ids?.length}
        <p class="ids" title={standing.row.local_ids.join(', ')}>{standing.row.local_ids[0]}</p>
      {/if}
    </header>

    {#if read.state === 'error' && read.error}
      <div class="trouble">
        <p class="warn">{explainError(read.error)}</p>
        <div class="act">
          <Button variant="outline" size="sm" onclick={read.retry}>Try again</Button>
        </div>
      </div>
    {/if}

    {#if settings === null}
      <Skeleton rows={5} height={24} />
    {:else}
      {#if standing.row}
        <WhyBars
          row={standing.row}
          weights={settings.weights}
          order={settings.order}
          labels={session.labels}
          {leader}
        />
      {:else}
        <p class="quiet">{standing.place === 'missing' ? NO_ROW_SERVED : NO_ROW_YET}</p>
      {/if}

      {#if waiting(read.state, read.card)}
        <Skeleton rows={4} height={22} />
      {:else}
        <AbilityTable rows={abilities} needs={settings.needs} />
      {/if}

      {#if !priceUnknown}
        <PriceBlock {figures} error={read.priceError} onretry={read.retry} />
      {/if}

      <ServedBy
        entries={read.card?.served_by ?? []}
        trim={settings.prefixWeights}
        known={servedKnown}
      />

      <InspectorActions session={session} id={id} name={standing.name} />
    {/if}
  </div>
{/if}

<style>
  /*
   * The pane's own surface, reaching its edges: the slot pads 16px and this
   * takes that back, because the panel's background is the pane's background
   * and a 16px frame of something else around it would read as a border.
   */
  .root {
    display: flex;
    flex-direction: column;
    gap: 18px;
    margin: -16px;
    padding: 16px 20px;
    min-height: calc(100% + 32px);
    background: var(--c-pane);
  }

  .head {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .caps {
    margin: 0;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-muted);
  }
  h2 {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: var(--c-ink);
    overflow-wrap: anywhere;
  }
  .ids {
    margin: 0;
    font-family: var(--f-mono);
    font-size: 12px;
    color: var(--c-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .trouble {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .warn {
    margin: 0;
    font-size: 12px;
    line-height: 1.45;
    color: var(--c-warn);
  }
  .act {
    display: flex;
  }
  .quiet {
    margin: 0;
    font-size: 13px;
    line-height: 1.45;
    color: var(--c-muted);
    max-width: 62ch;
  }
</style>
