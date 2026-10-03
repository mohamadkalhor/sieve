/**
 * Efforts across the seats (EFFORT.md section 6): the seat list's second line
 * and its amber `effort?`, the status bar's count, and the palette's
 * `effort <level>` for the open seat.
 */
import { describe, expect, it, vi } from 'vitest';
import type { SeatRow } from '../../src/lib/api/client';
import type { SeatSessionLike } from '../../src/lib/console/contracts';
import { effortCommands } from '../../src/lib/console/palette/providers';
import { effortNote, secondLine } from '../../src/lib/console/seats/view';
import { noEffort } from '../../src/lib/console/shell/statusbar';

function seat(over: Partial<SeatRow> = {}): SeatRow {
  return {
    name: 'build',
    modality: 'llm',
    purpose: 'the coder',
    mode: 'auto',
    ship: 4,
    live: [{ id: 'cx/gpt-6-astra', name: 'GPT-6 Astra' }],
    lineup: null,
    in_step: null,
    changes: null,
    shipped_at: null,
    ...over
  };
}

describe('the seat list', () => {
  it('says the model and the effort', () => {
    expect(secondLine(seat({ effort: 'medium' }))).toBe('GPT-6 Astra · medium');
    expect(secondLine(seat({ effort: 'non-reasoning' }))).toBe('GPT-6 Astra · none');
    expect(secondLine(seat({ effort: null }))).toBe('GPT-6 Astra');
    expect(secondLine(seat({ live: null, effort: 'medium' }))).toBeNull();
  });

  it('asks for an effort only where one would change the score', () => {
    expect(effortNote(seat({ effort: null, multi_mode: true }))?.text).toBe('effort?');
    expect(effortNote(seat({ effort: null, multi_mode: true }))?.title).toBe(
      'Runs at is not set: models are scored at their top effort'
    );
    expect(effortNote(seat({ effort: 'medium', multi_mode: true }))).toBeNull();
    expect(effortNote(seat({ effort: null, multi_mode: false }))).toBeNull();
    // a server from before efforts says neither, and is not nagged
    expect(effortNote(seat())).toBeNull();
    expect(effortNote(seat({ modality: 'text-to-image', multi_mode: true }))).toBeNull();
  });
});

describe('the status bar count', () => {
  it('counts the seats with no effort set and links the first', () => {
    const rows = [
      seat({ name: 'build', effort: 'medium', multi_mode: true }),
      seat({ name: 'critic', effort: null, multi_mode: true }),
      seat({ name: 'designer', effort: null, multi_mode: true })
    ];
    expect(noEffort(rows)).toEqual({ text: '2 seats have no effort set', href: '/seats/critic' });
    expect(noEffort(rows.slice(0, 2))?.text).toBe('1 seat has no effort set');
  });

  it('says nothing when none is missing, or before the list answers', () => {
    expect(noEffort([seat({ effort: 'high', multi_mode: true })])).toBeNull();
    expect(noEffort(null)).toBeNull();
  });
});

describe('effort <level> in the palette', () => {
  function session(over: Record<string, unknown> = {}): SeatSessionLike {
    return {
      name: 'build',
      profile: { modality: 'llm' },
      settings: { effort: 'medium' },
      edit: vi.fn(),
      setEffort: vi.fn(),
      ...over
    } as unknown as SeatSessionLike;
  }

  it('offers every other effort for the open seat', () => {
    const titles = effortCommands(session()).map((command) => command.title);
    expect(titles).toEqual([
      'effort any',
      'effort none',
      'effort minimal',
      'effort low',
      'effort high',
      'effort xhigh',
      'effort max'
    ]);
  });

  it('runs through the session, so the hint can count the move', () => {
    const open = session();
    effortCommands(open).find((command) => command.title === 'effort high')?.run();
    expect(open.setEffort).toHaveBeenCalledWith('high');
    effortCommands(open).find((command) => command.title === 'effort any')?.run();
    expect(open.setEffort).toHaveBeenCalledWith(null);
  });

  it('offers nothing for a media seat or before the settings arrive', () => {
    expect(effortCommands(session({ profile: { modality: 'text-to-image' } }))).toEqual([]);
    expect(effortCommands(session({ settings: null }))).toEqual([]);
  });
});
