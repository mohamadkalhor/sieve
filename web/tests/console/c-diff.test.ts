import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { diffLineup, shipLabel } from '../../src/lib/console/logic/diff';

/**
 * Two orders of the same seat, against the fixture both sides share.
 *
 * The fixture exists so the browser and the server assert the same arithmetic
 * against one file rather than against each other (CONSOLE.md section 4.1):
 * `sieve/scoring/select.py:changes()` and this module must agree on all eight
 * cases. The three null cases are the other half of the rule -- a side nobody
 * stored is null, not empty, and "no lineup" must never be drawn as "nothing
 * ships".
 */

interface Case {
  name: string;
  live: string[] | null;
  lineup: string[] | null;
  changes: number | null;
  in_step: boolean | null;
}

const fixture = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../../tests/fixtures/lineup_diff.json', import.meta.url)), 'utf8')
) as { cases: Case[]; null_cases: Case[] };

/** What the server compares for `in_step`: same ids, same order, both known. */
function inStep(test: Case): boolean | null {
  if (test.live === null || test.lineup === null) return null;
  return (
    test.live.length === test.lineup.length &&
    test.live.every((id, index) => id === test.lineup?.[index])
  );
}

describe('diffLineup', () => {
  it.each(fixture.cases)('$name', (test) => {
    const diff = diffLineup(test.live, test.lineup);
    expect(diff.known).toBe(true);
    expect(diff.changes).toBe(test.changes);
    // the fixture's own in_step, which the seats list sends and this module
    // deliberately does not: "no badge" and "0 changes" are different answers
    expect(inStep(test)).toBe(test.in_step);
  });

  it.each(fixture.null_cases)('$name', (test) => {
    const diff = diffLineup(test.live, test.lineup);
    expect(diff.known).toBe(false);
    expect(diff.changes).toBeNull();
    expect(inStep(test)).toBe(test.in_step);
    // an unknown pair makes no claim about any row: no "new" for a whole list
    expect(diff.moves).toEqual({});
    expect(diff.leaving).toEqual([]);
  });

  it('counts a move once per id, as the server does', () => {
    const swapped = diffLineup(['a', 'b', 'c', 'd'], ['b', 'a', 'c', 'd']);
    // two ids traded places; the other two did not move and are not counted
    expect(swapped.changes).toBe(2);
    expect(swapped.moves.a).toEqual({ id: 'a', kind: 'down', by: 1, text: 'down 1' });
    expect(swapped.moves.b).toEqual({ id: 'b', kind: 'up', by: 1, text: 'up 1' });
    expect(swapped.moves.c).toEqual({ id: 'c', kind: 'same', by: 0, text: '' });

    // a full reversal moves every id in it, so all four are counted
    const reversed = diffLineup(['a', 'b', 'c', 'd'], ['d', 'c', 'b', 'a']);
    expect(reversed.changes).toBe(4);
    expect(reversed.moves.a).toEqual({ id: 'a', kind: 'down', by: 3, text: 'down 3' });
    expect(reversed.moves.d).toEqual({ id: 'd', kind: 'up', by: 3, text: 'up 3' });
  });

  it('names a row the live chain does not hold as new, and nothing else', () => {
    const diff = diffLineup(['a', 'b'], ['b', 'c', 'a']);
    expect(diff.moves.c).toEqual({ id: 'c', kind: 'new', by: 0, text: 'new' });
    expect(diff.moves.a).toEqual({ id: 'a', kind: 'down', by: 2, text: 'down 2' });
    expect(diff.moves.b).toEqual({ id: 'b', kind: 'up', by: 1, text: 'up 1' });
    expect(diff.changes).toBe(3);
    expect(diff.leaving).toEqual([]);
  });

  it('lists what the live chain holds and the lineup does not', () => {
    const diff = diffLineup(['a', 'b', 'c'], ['b', 'c']);
    expect(diff.leaving).toEqual(['a']);
    // leaving ids keep their live order, so the list reads as the chain does
    expect(diff.moves.a).toBeUndefined();
  });

  it('does not repeat an id that leaves and comes back', () => {
    const diff = diffLineup(['a', 'b'], ['a', 'b']);
    expect(diff.leaving).toEqual([]);
    expect(diff.changes).toBe(0);
  });
});

describe('shipLabel', () => {
  const known = diffLineup(['a', 'b', 'c'], ['c', 'a', 'b']);
  const same = diffLineup(['a', 'b'], ['a', 'b']);
  const unknown = diffLineup(null, ['a', 'b']);

  it('says how much would change', () => {
    expect(shipLabel(known, { disabled: false, label: 'Ship now' })).toBe('Ship 3 changes');
  });

  it('counts one change in the singular', () => {
    const one = diffLineup(['a', 'b'], ['a', 'b', 'c']);
    expect(shipLabel(one, { disabled: false, label: 'Ship now' })).toBe('Ship 1 change');
  });

  it('leaves the button alone when it is disabled, or nothing would change', () => {
    expect(shipLabel(known, { disabled: true, label: 'this is already what ships' })).toBe(
      'this is already what ships'
    );
    expect(shipLabel(same, { disabled: false, label: 'Ship now' })).toBe('Ship now');
  });

  it('claims nothing when the two sides were never compared', () => {
    expect(shipLabel(unknown, { disabled: false, label: 'Ship now' })).toBe('Ship now');
  });
});
