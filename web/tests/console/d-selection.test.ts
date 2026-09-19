/**
 * The selection: `?model=`, and what it does when the pool changes under it
 * (CONSOLE.md section 5.3).
 */
import { describe, expect, it } from 'vitest';
import { Selection } from '../../src/lib/console/state/selection.svelte';
import type { SeatSessionLike } from '../../src/lib/console/contracts';
import type { Listed } from '../../src/lib/api/client';

function row(id: string): Listed {
  return { id, name: id } as Listed;
}

/** The smallest thing `adopt` reads: live, preview, and nothing else. */
function seat(live: string[] | null, lineup: string[], pool: string[] = lineup): SeatSessionLike {
  return {
    live,
    preview: { models: lineup.map(row), pool: pool.map(row) } as never
  } as unknown as SeatSessionLike;
}

function selection(at: string | null = null) {
  const written: (string | null)[] = [];
  const it = new Selection({
    read: () => at,
    replace: (id) => written.push(id)
  });
  return { selection: it, written };
}

describe('Selection', () => {
  it('starts from the address bar', () => {
    expect(selection('a/b').selection.id).toBe('a/b');
    expect(selection().selection.id).toBe(null);
  });

  it('writes ?model= with replace, and only when it changes', () => {
    const { selection: sel, written } = selection();
    sel.select('a/b');
    expect(sel.id).toBe('a/b');
    sel.select('a/b');
    sel.select(null);
    expect(written).toEqual(['a/b', null]);
  });

  it('keeps a ?model= that is in the pool', () => {
    const { selection: sel, written } = selection('a/b');
    sel.adopt(seat(['a/b'], ['a/b', 'c/d']));
    expect(sel.id).toBe('a/b');
    expect(written).toEqual([]);
  });

  it('replaces a ?model= that is not in the pool with the first moved row', () => {
    // The old selection is gone, so the lineup decides: the first row that
    // moved is the one the person is most likely asking about.
    const { selection: sel, written } = selection('gone');
    sel.adopt(seat(['a/b'], ['a/b', 'c/d']));
    expect(sel.id).toBe('c/d');
    expect(written).toEqual(['c/d']);
  });

  it('replaces a stale ?model= with the first row when nothing moved', () => {
    const { selection: sel, written } = selection('gone');
    sel.adopt(seat(['a/b', 'c/d'], ['a/b', 'c/d']));
    expect(sel.id).toBe('a/b');
    expect(written).toEqual(['a/b']);
  });

  it('prefers a row that moved to the first row', () => {
    // c/d is new, so it is the row the person is most likely asking about.
    const { selection: sel } = selection();
    sel.adopt(seat(['a/b'], ['c/d', 'a/b']));
    expect(sel.id).toBe('c/d');
  });

  it('falls back to the first row when nothing moved', () => {
    const { selection: sel } = selection();
    sel.adopt(seat(['a/b', 'c/d'], ['a/b', 'c/d']));
    expect(sel.id).toBe('a/b');
  });

  it('says nothing when there is no answer yet', () => {
    const { selection: sel, written } = selection();
    sel.adopt({ live: ['a/b'], preview: null } as unknown as SeatSessionLike);
    expect(sel.id).toBe(null);
    expect(written).toEqual([]);
  });

  it('clears when the lineup is empty', () => {
    const { selection: sel, written } = selection('a/b');
    sel.adopt(seat(['a/b'], [], []));
    expect(sel.id).toBe(null);
    expect(written).toEqual([null]);
  });

  it('treats an unknown live chain as no movement to prefer', () => {
    const { selection: sel } = selection('gone');
    sel.adopt(seat(null, ['a/b', 'c/d']));
    expect(sel.id).toBe('a/b');
  });
});
