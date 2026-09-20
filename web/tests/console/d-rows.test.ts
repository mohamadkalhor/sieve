/**
 * The table's view model (CONSOLE.md section 6.6, REVIEW.md finding 10).
 *
 * The named cases of finding 10 are here -- an empty hand list, a lineup emptied
 * by removals, and a leaving id nothing reachable serves -- because those are
 * exactly the three places where a row or a sentence could claim more than the
 * server said.
 */
import { describe, expect, it } from 'vitest';
import type { Listed, PreviewResult } from '../../src/lib/api/client';
import { diffLineup } from '../../src/lib/console/logic/diff';
import {
  actionLabel,
  canDo,
  emptyText,
  idsOf,
  poolRows,
  standing,
  stepIndex,
  tabStop,
  tableSections,
  unlinkedRows,
  unscoredCount,
  via
} from '../../src/lib/console/seat/rows';

function row(id: string, over: Partial<Listed> = {}): Listed {
  return { id, name: id, local_ids: [`or/${id}`], score: 0.5, ...over };
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

const noDiff = diffLineup(null, null);

describe('tableSections', () => {
  it('ranks the lineup 1..n and numbers the next rows after it', () => {
    const sections = tableSections(
      preview({ models: [row('a'), row('b')], next: [row('c'), row('d')] }),
      noDiff
    );
    expect(sections.lineup.map((r) => [r.rank, r.id])).toEqual([
      ['1', 'a'],
      ['2', 'b']
    ]);
    expect(sections.next.map((r) => [r.rank, r.id])).toEqual([
      ['3', 'c'],
      ['4', 'd']
    ]);
  });

  it('leaves the rows outside the ranking without a number', () => {
    const sections = tableSections(
      preview({ models: [row('a')], blocked: [row('b')], removed: [row('c')], missing: [{ id: 'd', name: 'D' }] }),
      noDiff
    );
    expect(sections.blocked[0].rank).toBe('');
    expect(sections.removed[0].rank).toBe('');
    expect(sections.missing[0].rank).toBe('');
  });

  it('has nothing at all to say before an answer arrives', () => {
    const sections = tableSections(null, noDiff);
    expect(idsOf(sections)).toEqual([]);
  });

  it('draws a leaving model under the line, with the word "leaves"', () => {
    const sections = tableSections(
      preview({ models: [row('a')] }),
      diffLineup(['a', 'z'], ['a'])
    );
    expect(sections.leaving.map((r) => [r.id, r.note])).toEqual([['z', 'leaves']]);
  });

  it('deduplicates a leaving id against every displayed list, not only next', () => {
    const sections = tableSections(
      preview({
        models: [row('a')],
        next: [row('b')],
        blocked: [row('c')],
        removed: [row('d')],
        missing: [{ id: 'e', name: 'E' }]
      }),
      diffLineup(['a', 'b', 'c', 'd', 'e', 'z'], ['a'])
    );
    expect(sections.leaving.map((r) => r.id)).toEqual(['z']);
  });

  it('does not repeat a leaving id the hand list is already showing', () => {
    const sections = tableSections(
      preview({ models: [row('a')] }),
      diffLineup(['a', 'z'], ['a']),
      ['z']
    );
    expect(sections.leaving).toEqual([]);
  });

  it('keeps a leaving model the pool still holds as a ranked row', () => {
    const sections = tableSections(
      preview({ models: [row('a')], pool: [row('z', { score: 0.31 })] }),
      diffLineup(['a', 'z'], ['a'])
    );
    expect(sections.leaving[0].kind).toBe('ranked');
    expect(sections.leaving[0].note).toBe('leaves');
  });

  it('an unreachable leaving id is a bare row: no score, no cost, no capabilities', () => {
    const sections = tableSections(preview({ models: [row('a')] }), diffLineup(['a', 'ghost/model'], ['a']));
    const leaving = sections.leaving[0];
    expect(leaving.kind).toBe('bare');
    expect(leaving).toMatchObject({ id: 'ghost/model', name: 'ghost/model', why: 'leaving' });
    expect('row' in leaving).toBe(false);
  });
});

describe('emptyText', () => {
  it('names the needs only when the answer says needs are what emptied it', () => {
    expect(emptyText(preview({ failed_needs: 2 }), 'auto')).toBe(
      'Nothing ships: no reachable model meets every need.'
    );
  });

  it('an empty hand list is not a needs failure', () => {
    expect(emptyText(preview({ failed_needs: 3, models: [] }), 'manual')).toBe(
      'Nothing would ship with these settings.'
    );
  });

  it('a lineup emptied by removals says the neutral sentence', () => {
    expect(emptyText(preview({ failed_needs: 1, removed: [row('a')] }), 'auto')).toBe(
      'Nothing would ship with these settings.'
    );
  });

  it('an empty answer with no reason says the neutral sentence', () => {
    expect(emptyText(preview({ failed_needs: 0 }), 'auto')).toBe('Nothing would ship with these settings.');
    expect(emptyText(null, 'auto')).toBe('Nothing would ship with these settings.');
  });
});

describe('standing', () => {
  const at = {
    lineup: ['a', 'b'],
    pinned: ['b', 'c'],
    removed: ['d'],
    needs: ['tools'] as const,
    lacks: ['tools'] as const
  };

  it('ports the five answers of the page it replaces', () => {
    expect(standing('a', { ...at, lacks: [] })).toBe('ships #1');
    expect(standing('b', at)).toBe('pinned · ships #2');
    expect(standing('c', at)).toBe('pinned · skipped');
    expect(standing('d', at)).toBe('removed');
    expect(standing('e', { ...at, lacks: [] })).toBe('not shipping');
    expect(standing('e', at)).toBe('fails Must support');
  });

  it('does not blame a need nobody asked for', () => {
    expect(standing('e', { ...at, needs: [] })).toBe('not shipping');
  });
});

describe('canDo', () => {
  it('lists what it is known to do, in the need order', () => {
    expect(canDo(row('a', { abilities: { tools: true, vision: true, reasoning: false } }), []).text).toBe(
      'vision · tools'
    );
  });

  it('says unknown for a required need nobody answered for', () => {
    const said = canDo(row('a', { abilities: { tools: null, vision: true } }), ['tools']);
    expect(said).toEqual({ text: 'vision · tools unknown', warn: true, title: '' });
  });

  it('a field the server does not have is not an answer', () => {
    expect(canDo(row('a'), ['tools'])).toEqual({
      text: '',
      warn: false,
      title: 'the server does not report capabilities'
    });
    expect(canDo(row('a', { abilities: {} }), [])).toEqual({
      text: '',
      warn: false,
      title: 'no source says it can do any of these'
    });
  });
});

describe('actionLabel', () => {
  it('names the model in every row button', () => {
    expect(actionLabel('pin', 'Opus')).toBe('Pin Opus');
    expect(actionLabel('pin', 'Opus', true)).toBe('Unpin Opus');
    expect(actionLabel('remove', 'Opus')).toBe('Never ship Opus');
    expect(actionLabel('drop', 'Opus')).toBe('Take Opus off the list');
  });
});

describe('via', () => {
  it('names the routers, two at most', () => {
    expect(via(['or/a/b'])).toBe('or/');
    expect(via(['or/a', 'cli/a'])).toBe('or/ cli/');
    expect(via(['or/a', 'cli/a', 'gq/a'])).toBe('or/ cli/ +1');
    expect(via([])).toBe('');
  });
});

describe('poolRows', () => {
  const pool = [
    row('a', { name: 'Alpha', local_ids: ['or/alpha'], score: 0.9, abilities: { tools: true } }),
    row('b', { name: 'Beta', local_ids: ['cli/beta'], scored: false }),
    row('c', { name: 'Gamma', lacks: ['tools'], abilities: { tools: false } })
  ];

  it('keeps the pool order and matches name, id and local id', () => {
    expect(poolRows(pool).rows.map((r) => r.id)).toEqual(['a', 'b', 'c']);
    expect(poolRows(pool, { needle: 'alph' }).rows.map((r) => r.id)).toEqual(['a']);
    expect(poolRows(pool, { needle: 'cli/' }).rows.map((r) => r.id)).toEqual(['b']);
    expect(poolRows(pool, { needle: 'GAM' }).rows.map((r) => r.id)).toEqual(['c']);
  });

  it('shows the unscored ones alone when asked', () => {
    expect(poolRows(pool, { unscoredOnly: true }).rows.map((r) => r.id)).toEqual(['b']);
  });

  it('filters by need in manual mode only: auto has to show what a need keeps out', () => {
    expect(poolRows(pool, { mode: 'manual', needs: ['tools'] }).rows.map((r) => r.id)).toEqual(['a']);
    expect(poolRows(pool, { mode: 'auto', needs: ['tools'] }).rows.map((r) => r.id)).toEqual(['a', 'b', 'c']);
  });

  it('stops at the cap and says how many were left', () => {
    const many = Array.from({ length: 205 }, (_, i) => row(`m${i}`));
    expect(poolRows(many).rows).toHaveLength(200);
    expect(poolRows(many).more).toBe(5);
  });
});

describe('unlinkedRows', () => {
  const unlinked = [{ local_id: 'or/mystery', name: 'Mystery' }];

  it('is silent until somebody is looking for something', () => {
    expect(unlinkedRows(unlinked)).toEqual([]);
    expect(unlinkedRows(unlinked, { needle: 'myst' })).toHaveLength(1);
    expect(unlinkedRows(unlinked, { unscoredOnly: true })).toHaveLength(1);
  });
});

describe('unscoredCount', () => {
  it('counts unscored pool rows and unlinked ids', () => {
    expect(
      unscoredCount(preview({ pool: [row('a', { scored: false }), row('b')], unlinked: [{ local_id: 'x', name: 'X' }] }))
    ).toBe(2);
    expect(unscoredCount(null)).toBe(0);
  });
});

describe('keyboard moves', () => {
  it('clamps rather than wrapping', () => {
    expect(stepIndex(0, 3, -1)).toBe(0);
    expect(stepIndex(2, 3, 1)).toBe(2);
    expect(stepIndex(0, 3, 1)).toBe(1);
    expect(stepIndex(-1, 3, 1)).toBe(0);
    expect(stepIndex(-1, 0, 1)).toBe(-1);
  });

  it('keeps one tab stop: the moved-to row, else the selected one, else the first', () => {
    expect(tabStop(['a', 'b'], 'b', 'a')).toBe('b');
    expect(tabStop(['a', 'b'], 'gone', 'b')).toBe('b');
    expect(tabStop(['a', 'b'], null, null)).toBe('a');
    expect(tabStop([], null, null)).toBe(null);
  });
});
