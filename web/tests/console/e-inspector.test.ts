/**
 * The inspector's view model and its card cache (CONSOLE.md sections 4.3, 5.3
 * and 6.7, REVIEW.md finding 9).
 *
 * The cases worth naming here are the ones where a panel could say more than
 * the server did: a price lookup that failed is not "No posted price", an
 * unmeasured axis is not a bar of zero, an unanswered need is not a "no", and
 * the caps line's "ships #2" is only sayable when the lineup was actually read.
 */
import { describe, expect, it } from 'vitest';
import type {
  ApiError,
  Listed,
  ModelCard,
  ModelRow,
  PreviewResult,
  Result
} from '../../src/lib/api/client';
import { CardCache, prefixOf, rowAbilities } from '../../src/lib/console/state/card.svelte';
import {
  abilityRows,
  allUnknown,
  barWidth,
  capsText,
  fitLine,
  inStep,
  oneDecimal,
  points,
  priceFigures,
  standingOf,
  twoDecimals,
  unitWording,
  unknownRequired,
  waiting,
  whyHeading
} from '../../src/lib/console/inspector/view';

/* -------------------------------------------------------------------------- */
/* fixtures                                                                    */
/* -------------------------------------------------------------------------- */

function row(id: string, over: Partial<Listed> = {}): Listed {
  return { id, name: id, local_ids: [`or/${id}`], score: 0.88, ...over };
}

function preview(over: Partial<PreviewResult> = {}): PreviewResult {
  return {
    profile: 'coder',
    ship: 2,
    models: [],
    next: [],
    settings: { ship: 2, weights: {} },
    computed_at: '2026-01-01T00:00:00Z',
    warnings: [],
    ...over
  } as PreviewResult;
}

function card(over: Partial<ModelCard> = {}): ModelCard {
  return {
    id: 'a',
    name: 'A',
    creator: 'C',
    modality: 'llm',
    effort: null,
    family: null,
    price: {
      unit: 'usd_per_1m_tokens',
      input: 1.25,
      output: 5,
      per_unit: null,
      source: 'openrouter',
      observed_at: '2026-01-01T00:00:00Z'
    },
    abilities: {
      vision: { answer: true, yes: ['inventory: openrouter'], no: [] },
      reasoning: { answer: false, yes: [], no: ['inventory: models.dev'] },
      tools: { answer: null, yes: [], no: [] },
      structured_output: { answer: true, yes: [], no: [] }
    },
    context_window: 128000,
    served_by: [{ local_id: 'or/a', prefix: 'or', inventory: 'openrouter', stale: false }],
    scored: true,
    ...over
  };
}

const failure = (status: number, code = 'nope'): ApiError => ({
  code,
  message: `the server said ${status}`,
  status
});

/** A client that answers from a script, and counts what it was asked. */
function client(script: {
  card?: Result<ModelCard>;
  pages?: Result<{ items: ModelRow[]; next_cursor: string | null }>[];
}) {
  const calls = { card: 0, pages: 0, asked: [] as string[] };
  let page = 0;
  return {
    calls,
    api: {
      async modelCard(id: string, modality: string): Promise<Result<ModelCard>> {
        calls.card += 1;
        calls.asked.push(`${modality}:${id}`);
        return script.card ?? { ok: false, error: failure(404, 'no_card') };
      },
      async models(): Promise<Result<{ items: ModelRow[]; next_cursor: string | null }>> {
        calls.pages += 1;
        const answer = script.pages?.[page] ?? {
          ok: true,
          value: { items: [], next_cursor: null }
        };
        page += 1;
        return answer;
      }
    },
    token: () => undefined
  };
}

/** Let every promise a read is waiting on run out. */
const settle = (): Promise<void> => new Promise((done) => setTimeout(done, 0));

/* -------------------------------------------------------------------------- */
/* where it stands                                                             */
/* -------------------------------------------------------------------------- */

describe('standingOf', () => {
  it('finds a lineup row and counts its place from one', () => {
    const standing = standingOf('b', preview({ models: [row('a'), row('b')] }));
    expect(standing.place).toBe('lineup');
    expect(standing.rank).toBe(2);
    expect(standing.name).toBe('b');
  });

  it('numbers a `next` row by where it would land if the lineup shipped', () => {
    const standing = standingOf('d', preview({ models: [row('a'), row('b')], next: [row('c'), row('d')] }));
    expect(standing.place).toBe('next');
    expect(standing.rank).toBe(4);
  });

  it('reads the lists in the order a person would, not the order they arrived', () => {
    // `pool` holds every reachable model, so a blocked row is in both lists.
    const both = preview({ blocked: [row('z')], pool: [row('z')] });
    expect(standingOf('z', both).place).toBe('blocked');
  });

  it('keeps a missing id by name, with no row to draw', () => {
    const standing = standingOf('gone', preview({ missing: [{ id: 'gone', name: 'Gone' }] }));
    expect(standing.place).toBe('missing');
    expect(standing.row).toBeNull();
    expect(standing.name).toBe('Gone');
  });

  it('has nowhere to put a selection before an answer arrives', () => {
    expect(standingOf('a', null).place).toBe('none');
  });
});

describe('the caps line', () => {
  const lineup = preview({ models: [row('a'), row('b')] });

  it('says "ships" only when both sides were read and agree', () => {
    const standing = standingOf('b', lineup);
    expect(capsText(standing, inStep({ known: true, changes: 0 } as never))).toBe('Selected · ships #2');
    expect(capsText(standing, inStep({ known: true, changes: 1 } as never))).toBe(
      'Selected · would ship #2'
    );
    expect(capsText(standing, inStep({ known: false, changes: 0 } as never))).toBe(
      'Selected · would ship #2'
    );
  });

  it('names the reason a model is not shipping, one reason per line', () => {
    expect(capsText(standingOf('z', preview({ blocked: [row('z')] })), true)).toBe(
      'Selected · fails Must support'
    );
    expect(capsText(standingOf('z', preview({ removed: [row('z')] })), true)).toBe(
      'Selected · removed by you'
    );
    expect(
      capsText(standingOf('gone', preview({ missing: [{ id: 'gone', name: 'Gone' }] })), true)
    ).toBe('Selected · nothing reachable serves it');
    expect(capsText(standingOf('p', preview({ pool: [row('p')] })), true)).toBe(
      'Selected · not shipping'
    );
    expect(capsText(standingOf('p', preview({ pool: [row('p', { pinned: true })] })), true)).toBe(
      'Selected · pinned · skipped'
    );
    expect(capsText(standingOf('d', preview({ models: [row('a')], next: [row('d')] })), true)).toBe(
      'Selected · next up, #2'
    );
  });
});

/* -------------------------------------------------------------------------- */
/* the bars                                                                    */
/* -------------------------------------------------------------------------- */

describe('the numbers the bars draw', () => {
  it('sizes a bar against the widest axis and clamps both ends', () => {
    expect(barWidth(0.911)).toBe('91%');
    expect(barWidth(1.4)).toBe('100%');
    expect(barWidth(-0.2)).toBe('0%');
  });

  it('never puts NaN or Infinity on the page', () => {
    expect(barWidth(Number.NaN)).toBe('0%');
    // a share that is not a finite number is not a full bar either
    expect(barWidth(Number.POSITIVE_INFINITY)).toBe('0%');
    expect(oneDecimal(Number.NaN)).toBe('—');
    expect(points(Number.NaN)).toBe('—');
    expect(twoDecimals(Number.POSITIVE_INFINITY)).toBe('—');
  });

  it('prints a points figure without a float tail', () => {
    expect(points(0.45 * 100)).toBe('45');
    expect(points(42.5)).toBe('42.5');
    expect(oneDecimal(41.04)).toBe('41.0');
    expect(twoDecimals(0.9)).toBe('0.90');
  });

  it('heads the block with the raw score, and writes the equation to the final', () => {
    // the bars under the heading are the raw axes, so the heading is the raw
    // score and the line under them is what turned it into `score` (finding 11)
    expect(whyHeading(row('a', { raw: 0.9, score: 0.88 }).raw ?? 0)).toBe('Where 0.90 comes from');
    expect(whyHeading(0.88)).toBe('Where 0.88 comes from');
    expect(whyHeading(Number.NaN)).toBe('Where — comes from');
    expect(fitLine(row('a', { raw: 0.9, score: 0.88 }))).toBeNull();
    expect(fitLine(row('a', { raw: 0.9, score: 0.846, health: 0.94 }))).toBe(
      '0.90 × health 0.94 × trim 1.00 = 0.85'
    );
    expect(fitLine(row('a', { raw: 0.9, score: 0.792, health: 0.88, factor: 1 }))).toBe(
      '0.90 × health 0.88 × trim 1.00 = 0.79'
    );
    // a server that does not report the raw score gets the fragment, never an
    // equation with a missing left-hand side
    expect(fitLine(row('a', { score: 0.9, health: 0.5 }))).toBe('× health 0.50 × trim 1.00');
    // a row that reports no multipliers has nothing to explain
    expect(fitLine(row('a'))).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/* the abilities                                                               */
/* -------------------------------------------------------------------------- */

describe('the ability table', () => {
  it('draws the card four times, with the sources that answered', () => {
    const rows = abilityRows(card(), null);
    expect(rows.map((entry) => entry.need)).toEqual([
      'vision',
      'reasoning',
      'tools',
      'structured_output'
    ]);
    expect(rows.map((entry) => entry.tone)).toEqual(['yes', 'no', 'unknown', 'yes']);
    expect(rows.map((entry) => entry.who)).toEqual([
      'openrouter',
      'models.dev',
      'no source says',
      'source not reported'
    ]);
    expect(rows[0].label).toBe('Image input');
  });

  it('reads a preview row as answers with no authors named', () => {
    const rows = abilityRows(null, row('a', { abilities: { tools: true } }));
    const tools = rows.find((entry) => entry.need === 'tools');
    const vision = rows.find((entry) => entry.need === 'vision');
    expect(tools?.tone).toBe('yes');
    // something answered, so it is not "no source says" -- it is "we do not know who"
    expect(tools?.who).toBe('source not reported');
    expect(vision?.tone).toBe('unknown');
    expect(vision?.who).toBe('no source says');
    expect(allUnknown(rows)).toBe(false);
    expect(allUnknown(abilityRows(null, row('a')))).toBe(true);
  });

  it('says so only when a need the seat requires is unknown', () => {
    const rows = abilityRows(card(), null);
    expect(unknownRequired(rows, ['tools'])).toBe(true);
    expect(unknownRequired(rows, ['vision'])).toBe(false);
    expect(unknownRequired(rows, [])).toBe(false);
  });

  it('turns a row into a card-shaped answer for every need, always all four', () => {
    const abilities = rowAbilities(row('a', { abilities: { vision: true } }));
    expect(Object.keys(abilities)).toEqual(['vision', 'reasoning', 'tools', 'structured_output']);
    expect(abilities.vision).toEqual({ answer: true, yes: [], no: [] });
    expect(abilities.reasoning).toEqual({ answer: null, yes: [], no: [] });
  });
});

/* -------------------------------------------------------------------------- */
/* the price                                                                   */
/* -------------------------------------------------------------------------- */

describe('the price block', () => {
  it('gives a token price three figures, the last one the seat own', () => {
    const figures = priceFigures(card().price, row('a', { cost_per_task: 0.42 }), 'coder');
    expect(figures.map((figure) => figure.label)).toEqual([
      'In, per 1M',
      'Out, per 1M',
      'One coder task'
    ]);
    expect(figures.map((figure) => figure.money.text)).toEqual(['$1.25', '$5.00', '$0.42']);
  });

  it('leaves the task figure a dash when the server does not report a cost', () => {
    const figures = priceFigures(card().price, row('a'), 'coder');
    expect(figures[2].money.text).toBe('—');
    expect(figures[2].money.title).toBe('server does not report task cost');
  });

  it('gives a media price one figure, in the unit own words', () => {
    const price = { ...card().price!, unit: 'usd_per_image' as const, input: null, output: null, per_unit: 0.04 };
    const figures = priceFigures(price, row('a'), 'coder');
    expect(figures).toHaveLength(1);
    expect(figures[0].label).toBe('per image');
    expect(figures[0].money.text).toBe('$0.040');
  });

  it('prints a unit it does not know as the server spelled it', () => {
    expect(unitWording('usd_per_1m_tokens')).toBe('per 1M tokens');
    expect(unitWording('usd_per_widget')).toBe('usd_per_widget');
  });

  it('has nothing to draw when there is no price at all', () => {
    expect(priceFigures(null, row('a'), 'coder')).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */
/* the card cache                                                              */
/* -------------------------------------------------------------------------- */

describe('the card cache', () => {
  it('keeps a card it read, and reads it once', async () => {
    const fake = client({ card: { ok: true, value: card() } });
    const cache = new CardCache(fake);
    const first = cache.get('a', 'llm', null);
    const second = cache.get('a', 'llm', null);
    expect(first.state).toBe('loading');
    expect(second.state).toBe('loading');
    await settle();
    expect(fake.calls.card).toBe(1);
    expect(cache.get('a', 'llm', null).state).toBe('ready');
    expect(cache.get('a', 'llm', null).card?.name).toBe('A');
    expect(fake.calls.card).toBe(1);
  });

  it('keys the same id by modality, because a card is about one of them', async () => {
    const fake = client({ card: { ok: true, value: card() } });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', null);
    await settle();
    cache.get('a', 'text-to-image', null);
    await settle();
    expect(fake.calls.asked).toEqual(['llm:a', 'text-to-image:a']);
  });

  it('falls back to the row, and looks the price up by exact id', async () => {
    const listing = (id: string, modality: string | null, input: number): ModelRow =>
      ({
        id,
        modality,
        name: id,
        creator: 'C',
        aliases: [],
        effort: null,
        family: null,
        reachable: true,
        local_ids: [`or/${id}`],
        price: { input, output: input * 4, per_unit: null, unit: 'usd_per_1m_tokens' }
      }) as ModelRow;

    const fake = client({
      card: { ok: false, error: failure(404, 'no_card') },
      pages: [
        {
          ok: true,
          value: {
            // a loose search: the first page is not the answer, and a row of
            // another modality is not either
            items: [listing('ab', 'llm', 9), listing('a', 'text-to-image', 7)],
            next_cursor: 'more'
          }
        },
        { ok: true, value: { items: [listing('a', 'llm', 1.25)], next_cursor: null } }
      ]
    });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', row('a', { abilities: { tools: true }, cost_per_task: 0.5 }));
    await settle();

    const read = cache.get('a', 'llm', row('a'));
    expect(read.state).toBe('fallback');
    expect(read.error).toBeNull();
    expect(read.priceError).toBeNull();
    expect(fake.calls.pages).toBe(2);
    expect(read.card?.price?.input).toBe(1.25);
    expect(read.card?.abilities.tools.answer).toBe(true);
    expect(read.card?.abilities.vision.answer).toBeNull();
    expect(read.card?.served_by.map((entry) => entry.local_id)).toEqual(['or/a']);
  });

  it('takes a listing that did not report a modality, and only the exact id', async () => {
    const listing = (id: string, modality: string | null, input: number): ModelRow =>
      ({
        id,
        modality,
        name: id,
        creator: 'C',
        aliases: [],
        effort: null,
        family: null,
        reachable: true,
        local_ids: [`or/${id}`],
        price: { input, output: input * 4, per_unit: null, unit: 'usd_per_1m_tokens' }
      }) as ModelRow;

    const fake = client({
      card: { ok: false, error: failure(404, 'no_card') },
      pages: [
        {
          ok: true,
          value: {
            // the store's rows leave modality null; a longer id is a different
            // model, and a null modality is a listing that did not report one
            items: [listing('a-longer', null, 9), listing('a', null, 2)],
            next_cursor: null
          }
        }
      ]
    });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', row('a'));
    await settle();

    const read = cache.get('a', 'llm', row('a'));
    expect(read.priceError).toBeNull();
    expect(read.card?.price?.input).toBe(2);
  });

  it('keeps a failed price lookup apart from a price nobody posted', async () => {
    const fake = client({
      card: { ok: false, error: failure(404, 'no_card') },
      pages: [{ ok: false, error: failure(500, 'boom') }]
    });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', row('a'));
    await settle();

    const read = cache.get('a', 'llm', row('a'));
    expect(read.state).toBe('fallback');
    expect(read.priceError?.status).toBe(500);
    // and the card says nothing about price rather than "no posted price"
    expect(read.card?.price).toBeNull();
    expect(priceFigures(read.card?.price ?? null, null, 'coder')).toEqual([]);
  });

  it('says a read failed, and keeps the card that was already there', async () => {
    const fake = client({ card: { ok: true, value: card() } });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', null);
    await settle();

    const wall = client({ card: { ok: false, error: failure(500, 'boom') } });
    const second = new CardCache(wall);
    second.get('a', 'llm', null);
    await settle();
    const read = second.get('a', 'llm', null);
    expect(read.state).toBe('error');
    expect(read.error?.message).toBe('the server said 500');
    expect(read.card).toBeNull();

    // the same cache, after a good read, keeps what it had
    const held = new CardCache(fake);
    held.get('a', 'llm', null);
    await settle();
    const stillThere = held.get('a', 'llm', null);
    expect(stillThere.card?.name).toBe('A');
    expect(waiting(stillThere.state, stillThere.card)).toBe(false);
  });

  it('asks again on retry, without blanking what is on screen', async () => {
    const fake = client({ card: { ok: false, error: failure(500, 'boom') } });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', null);
    await settle();
    const read = cache.get('a', 'llm', null);
    expect(read.state).toBe('error');
    read.retry();
    expect(fake.calls.card).toBe(2);
  });

  it('reads a bare fallback again when the row turns up', async () => {
    const fake = client({ card: { ok: false, error: failure(404, 'no_card') } });
    const cache = new CardCache(fake);
    cache.get('a', 'llm', null);
    await settle();
    expect(cache.get('a', 'llm', null).card?.abilities.tools.answer).toBeNull();

    cache.get('a', 'llm', row('a', { abilities: { tools: true } }));
    await settle();
    expect(fake.calls.card).toBe(2);
    expect(cache.get('a', 'llm', row('a')).card?.abilities.tools.answer).toBe(true);
  });

  it('takes the prefix of a router id as everything before its first slash', () => {
    expect(prefixOf('or/anthropic/claude')).toBe('or');
    expect(prefixOf('plain')).toBe('plain');
  });
});

describe('what a read in flight draws', () => {
  it('is a skeleton only when there is no card to draw yet', () => {
    expect(waiting('loading', null)).toBe(true);
    expect(waiting('loading', card())).toBe(false);
    expect(waiting('ready', null)).toBe(false);
    expect(waiting('error', null)).toBe(false);
  });
});
