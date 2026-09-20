/**
 * The seats list store: one request for the whole app, and what a failure does
 * to a list (CONSOLE.md sections 5.3 and 6.2).
 *
 * The request count is the point. The pane is drawn in two places and the
 * palette reads it too, so "one request paints the seats pane" is only true if
 * askers are joined onto the one in flight rather than each starting their own.
 */
import { describe, expect, it, vi } from 'vitest';
import { fail, ok, type ApiError, type Result, type SeatRow, type SeatsResult } from '../../src/lib/api/client';
import { SeatsStore } from '../../src/lib/console/state/seats.svelte';

function seat(name: string, over: Partial<SeatRow> = {}): SeatRow {
  return {
    name,
    modality: 'llm',
    purpose: 'code',
    mode: 'auto',
    ship: 5,
    live: [{ id: 'a', name: 'a' }],
    lineup: [{ id: 'a', name: 'a' }],
    in_step: true,
    changes: 0,
    shipped_at: null,
    ...over
  };
}

function answer(rows: SeatRow[], degraded = false): Result<SeatsResult> {
  return ok({ rows, degraded });
}

describe('the seats store', () => {
  it('joins three askers onto the one request in flight', async () => {
    const seats = vi.fn(async () => answer([seat('coder')]));
    const store = new SeatsStore({ api: { seats } });

    await Promise.all([store.load(), store.load(), store.load()]);

    expect(seats).toHaveBeenCalledTimes(1);
    expect(store.rows?.map((row) => row.name)).toEqual(['coder']);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it('keeps "nobody asked yet" apart from "there are none"', async () => {
    const store = new SeatsStore({ api: { seats: async () => answer([]) } });

    expect(store.rows).toBeNull();
    await store.load();

    expect(store.rows).toEqual([]);
    expect(store.degraded).toBe(false);
  });

  it('keeps the degraded note the server sent, next to the rows', async () => {
    const store = new SeatsStore({ api: { seats: async () => answer([seat('coder')], true) } });

    await store.load();

    expect(store.degraded).toBe(true);
    expect(store.rows).toHaveLength(1);
  });

  it('turns a failure into an error rather than an empty list', async () => {
    const error: ApiError = { code: 'server', message: 'the API said no', status: 500 };
    const store = new SeatsStore({ api: { seats: async () => fail<SeatsResult>(error) } });

    await store.load();

    expect(store.rows).toBeNull();
    expect(store.error).toEqual(error);
    expect(store.loading).toBe(false);
  });

  it('does not join a new ask onto a finished failure', async () => {
    const seats = vi
      .fn()
      .mockResolvedValueOnce(fail<SeatsResult>({ code: 'gone', message: 'no', status: 500 }))
      .mockResolvedValueOnce(answer([seat('coder')]));
    const store = new SeatsStore({ api: { seats } });

    await store.load();
    expect(store.error).not.toBeNull();

    // Retry is a second question, and the store has to be able to ask it.
    await store.load();

    expect(seats).toHaveBeenCalledTimes(2);
    expect(store.error).toBeNull();
    expect(store.rows).toHaveLength(1);
  });

  it('patches the one row a ship changed and leaves the others where they were', async () => {
    const store = new SeatsStore({
      api: {
        seats: async () =>
          answer([
            seat('coder', { changes: 3, in_step: false }),
            seat('writer', { modality: 'llm', changes: 0 })
          ])
      }
    });
    await store.load();
    const before = store.rows;

    store.patch('coder', { changes: 0, in_step: true, lineup: [{ id: 'a', name: 'a' }] });

    expect(store.rows?.[0]).toMatchObject({ name: 'coder', changes: 0, in_step: true });
    expect(store.rows?.[1]).toBe(before?.[1]);
  });

  it('has nothing to patch before an answer has arrived', () => {
    const store = new SeatsStore({ api: { seats: async () => answer([]) } });

    store.patch('coder', { changes: 0 });

    expect(store.rows).toBeNull();
  });
});
