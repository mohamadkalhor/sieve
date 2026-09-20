/**
 * The seats pane's rules (CONSOLE.md section 6.2), and the one line a row may
 * carry.
 *
 * `secondLine` is about absence: no chain was ever read is not the same news as
 * a seat that ships nothing, and a pane that drew "nothing shipped yet" for the
 * first would be answering a question nobody asked it.
 */
import { describe, expect, it } from 'vitest';
import type { SeatRow } from '../../src/lib/api/client';
import { COLLAPSED_KEY, parseCollapsed, secondLine, toggled } from '../../src/lib/console/seats/view';

function seat(over: Partial<SeatRow> = {}): SeatRow {
  return {
    name: 'coder',
    modality: 'llm',
    purpose: 'code',
    mode: 'auto',
    ship: 5,
    live: [],
    lineup: [],
    in_step: true,
    changes: 0,
    shipped_at: null,
    ...over
  };
}

describe('secondLine', () => {
  it('draws the first model the gateway holds', () => {
    expect(secondLine(seat({ live: [{ id: 'a', name: 'Coder' }, { id: 'b', name: 'Fallback' }] }))).toBe('Coder');
  });

  it('says a seat ships nothing, which is a fact about the seat', () => {
    expect(secondLine(seat({ live: [] }))).toBe('nothing shipped yet');
  });

  it('says nothing at all about a seat whose chain was never read', () => {
    expect(secondLine(seat({ live: null }))).toBeNull();
  });
});

describe('the collapsed groups', () => {
  it('reads back what it wrote', () => {
    expect(parseCollapsed(JSON.stringify(['llm', 'music']))).toEqual(['llm', 'music']);
  });

  it('keeps only the strings, whatever was in storage', () => {
    expect(parseCollapsed('[1,"llm",null,{"a":1}]')).toEqual(['llm']);
  });

  it('ignores a value this pane did not write rather than refusing to draw', () => {
    expect(parseCollapsed('not json at all')).toEqual([]);
    expect(parseCollapsed('{"llm":true}')).toEqual([]);
    expect(parseCollapsed(null)).toEqual([]);
    expect(parseCollapsed('')).toEqual([]);
  });

  it('adds and removes one group, and keeps the rest in place', () => {
    expect(toggled([], 'llm')).toEqual(['llm']);
    expect(toggled(['llm', 'music'], 'llm')).toEqual(['music']);
    expect(toggled(['music'], 'llm')).toEqual(['music', 'llm']);
  });

  it('remembers under a name of its own, not the seat name', () => {
    expect(COLLAPSED_KEY).toBe('sieve:seats-collapsed');
  });
});
