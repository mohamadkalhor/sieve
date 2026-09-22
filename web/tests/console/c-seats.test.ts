import { describe, expect, it } from 'vitest';
import type { SeatRow } from '../../src/lib/api/client';
import type { Modality } from '../../src/lib/types';
import {
  MODALITY_LABEL,
  badge,
  groupByModality,
  landingSeat
} from '../../src/lib/console/logic/seats';

/**
 * The seats list: the modality order it is grouped by, and the two things a
 * badge may say (CONSOLE.md section 6.2).
 *
 * A badge is a claim. "hand" is a claim about the saved settings, so a seat
 * whose settings could not be read may not carry it; a count is a claim about
 * the connector, so it may only appear when the server actually counted.
 */

function seat(over: Partial<SeatRow> & { name: string; modality: Modality }): SeatRow {
  return {
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

describe('groupByModality', () => {
  const rows = [
    seat({ name: 'a', modality: 'text-to-image' }),
    seat({ name: 'b', modality: 'llm' }),
    seat({ name: 'c', modality: 'music' }),
    seat({ name: 'd', modality: 'llm' })
  ];

  it('orders the groups by the catalogue, not by the rows', () => {
    expect(groupByModality(rows).map((group) => group.modality)).toEqual([
      'llm',
      'text-to-image',
      'music'
    ]);
  });

  it('leaves out a modality with no seats, and keeps the rows it was given', () => {
    const groups = groupByModality(rows);
    expect(groups).toHaveLength(3);
    expect(groups[0].rows.map((one) => one.name)).toEqual(['b', 'd']);
    for (const group of groups) expect(group.rows.length).toBeGreaterThan(0);
  });

  it('labels every group in the words the catalogue uses', () => {
    expect(MODALITY_LABEL.llm).toBe('Text');
    expect(MODALITY_LABEL['image-editing']).toBe('Image editing');
    for (const group of groupByModality(rows)) expect(group.label).toBe(MODALITY_LABEL[group.modality]);
  });

  it('has nothing to group when there are no seats', () => {
    expect(groupByModality([])).toEqual([]);
  });
});

describe('badge', () => {
  it('counts changes when the server counted them', () => {
    expect(badge(seat({ name: 'a', modality: 'llm', changes: 3, in_step: false }))).toEqual({
      text: '3',
      tone: 'accent'
    });
  });

  it('prefers the count over the hand badge', () => {
    const row = seat({ name: 'a', modality: 'llm', mode: 'manual', changes: 2, in_step: false });
    expect(badge(row)).toEqual({ text: '2', tone: 'accent' });
  });

  it('says nothing when the seat is in step', () => {
    expect(badge(seat({ name: 'a', modality: 'llm', changes: 0, in_step: true }))).toBeNull();
  });

  it('says nothing about a seat whose lineup was never read', () => {
    const unknown = seat({ name: 'a', modality: 'llm', live: null, lineup: null, in_step: null, changes: null });
    expect(badge(unknown)).toBeNull();
  });

  it('marks a hand-made list with the muted badge', () => {
    const hand = seat({ name: 'a', modality: 'llm', mode: 'manual', changes: 0, in_step: true });
    expect(badge(hand)).toEqual({ text: 'hand', tone: 'muted' });
  });

  it('marks a hand-made list whose changes were never read too', () => {
    const hand = seat({
      name: 'a',
      modality: 'llm',
      mode: 'manual',
      live: null,
      lineup: null,
      in_step: null,
      changes: null
    });
    expect(badge(hand)).toEqual({ text: 'hand', tone: 'muted' });
  });
});

describe('landingSeat', () => {
  const rows = [
    seat({ name: 'animate', modality: 'text-to-image' }),
    seat({ name: 'coder', modality: 'llm' }),
    seat({ name: 'writer', modality: 'llm' })
  ];

  it('opens the top of the list the pane draws, not the first row sent', () => {
    expect(landingSeat(rows, null)?.name).toBe('coder');
  });

  it('prefers the seat you last worked in, then one with changes waiting', () => {
    expect(landingSeat(rows, 'animate')?.name).toBe('animate');
    const waiting = rows.map((row) => (row.name === 'writer' ? { ...row, changes: 2 } : row));
    expect(landingSeat(waiting, 'gone')?.name).toBe('writer');
  });

  it('has nothing to open when there are no seats', () => {
    expect(landingSeat([], null)).toBeUndefined();
  });
});
