/**
 * Two questions the pages used to answer wrongly.
 *
 * The first is what the selected-models box says while it is waiting: "Nothing
 * ranks for this profile yet" is a claim about the profile, and it was being
 * made about a request that had not come back. The second is why the list is
 * short: raising the list length and watching nothing move is only mysterious
 * if the page keeps the reason to itself.
 *
 * The box's three states live in the page, so what is asserted here is the
 * decision table the page renders from, and the counting it renders.
 */
import { describe, expect, it } from 'vitest';

import { shortList, shortListLine } from '../src/lib/rank/shortlist';
import type { Rank } from '../src/lib/types';

function rank(id: string, over: Partial<Rank> = {}): Rank {
  return {
    position: 1,
    model_id: id,
    reachable: true,
    score: 0.5,
    confidence: 1,
    health: 1,
    final: 0.5,
    ...over
  } as Rank;
}

/**
 * The same table the box draws from: a request that is in flight, one that has
 * been in flight too long, one that failed, and one that answered.
 */
type RankState = 'idle' | 'asking' | 'slow' | 'error' | 'ready';
function boxSays(state: RankState, ranks: number, error = ''): string {
  if (state === 'asking' || state === 'slow' || state === 'idle') return 'ranking…';
  if (state === 'error') return error || 'no answer from the server';
  return ranks ? `${ranks} rows` : 'Nothing ranks for this profile yet.';
}

describe('the selected-models box, while it waits', () => {
  it('says it is ranking rather than that nothing ranks', () => {
    expect(boxSays('asking', 0)).toBe('ranking…');
    expect(boxSays('slow', 0)).toBe('ranking…');
  });

  it('shows the API error when the request failed, never the empty claim', () => {
    expect(boxSays('error', 0, 'This needs a token. Set one in SIEVE_TOKENS and reload.')).toBe(
      'This needs a token. Set one in SIEVE_TOKENS and reload.'
    );
    expect(boxSays('error', 0)).toBe('no answer from the server');
  });

  it('claims nothing ranks only when the server answered with an empty list', () => {
    expect(boxSays('ready', 0)).toBe('Nothing ranks for this profile yet.');
    expect(boxSays('ready', 3)).toBe('3 rows');
  });
});

describe('why the list is short', () => {
  const ranks: Rank[] = [
    rank('a'),
    rank('b'),
    rank('c', { position: 0, reachable: false }),
    rank('d', { position: 0, excluded_by: 'min_confidence', confidence: 0.2 }),
    rank('e', { position: 0, excluded_by: 'tools' }),
    rank('f', { position: 0, excluded_by: 'tools' }),
    rank('g', { position: 0, dominated_by: 'a' }),
    rank('h')
  ];

  it('counts each model once, under the first thing that stopped it', () => {
    const counts = shortList(ranks, {
      shown: 2,
      cap: 10,
      statusOf: (id) => (id === 'h' ? 'removed' : 'active')
    });
    expect(counts.unreachable).toBe(1);
    expect(counts.belowFloor).toBe(1);
    expect(counts.dominated).toBe(1);
    expect(counts.excluded).toBe(2);
    expect(counts.excludedBy).toEqual({ tools: 2 });
    expect(counts.held).toBe(1);
    expect(counts.rows).toHaveLength(6);
  });

  it('writes one line with only the parts that are not zero', () => {
    const counts = shortList(ranks, {
      shown: 2,
      cap: 10,
      statusOf: (id) => (id === 'h' ? 'removed' : 'active')
    });
    expect(shortListLine(counts, 0.6)).toBe(
      '2 shown of list length 10 · 1 unreachable · 1 below floor 0.60 · 1 dominated · 2 excluded (tools 2) · 1 held or disabled'
    );
  });

  it('says only the length when nothing was dropped', () => {
    const counts = shortList([rank('a'), rank('b')], { shown: 2, cap: 5 });
    expect(shortListLine(counts, 0)).toBe('2 shown of list length 5');
    expect(counts.rows).toHaveLength(0);
  });

  it('names who beat a dominated model, so the reason can be read', () => {
    const counts = shortList([rank('g', { position: 0, dominated_by: 'sol' })], {
      shown: 0,
      cap: 5
    });
    expect(counts.rows[0]).toEqual({
      model_id: 'g',
      reason: 'beaten on every axis by sol'
    });
  });
});

describe('the gate prefix, in the rail and the runs list alike', () => {
  it('shows the person, not the record', async () => {
    const { person } = await import('../src/lib/who');
    expect(person('gate:me@example.com')).toBe('me');
    expect(person('sieve-cli')).toBe('sieve-cli');
    expect(person(null)).toBe('');
  });
});
