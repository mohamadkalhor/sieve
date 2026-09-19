import { describe, expect, it } from 'vitest';
import { load as root } from '../../src/routes/+page';
import { load as profiles } from '../../src/routes/(app)/profiles/+page';
import { load as seatRedirect } from '../../src/routes/(app)/profiles/[name]/+layout';
import { load as chains } from '../../src/routes/(app)/chains/+page';
import { load as rankings } from '../../src/routes/(app)/rankings/+page';
import { load as chainOf } from '../../src/routes/(app)/chains/[profile]/+page';
import { load as rankingOf } from '../../src/routes/(app)/rankings/[profile]/+page';
import { load as seat } from '../../src/routes/(app)/seats/[name]/+page';

/**
 * The console moved its URLs, and old ones are in bookmarks.
 *
 * A redirect that silently stops redirecting is invisible: the page it lands on
 * may still render, just not the page anyone meant. So each one is asked
 * directly -- the `load` is called and the thrown redirect is read -- rather
 * than trusted to a comment.
 *
 * 307, not 301: these are temporary moves of the console's own addresses, and a
 * permanent one would be cached by browsers long after the next rename.
 */

const MOVED = 307;

function redirectOf(run: () => unknown): { status: number; location: string } {
  try {
    run();
  } catch (thrown) {
    const redirect = thrown as { status?: unknown; location?: unknown };
    if (typeof redirect?.status !== 'number' || typeof redirect.location !== 'string') {
      throw new Error(`that load threw something that is not a redirect: ${String(thrown)}`);
    }
    return { status: redirect.status, location: redirect.location };
  }
  throw new Error('that load returned instead of redirecting');
}

describe('the addresses the console moved', () => {
  it('opens on the seats list, not on the field', () => {
    expect(redirectOf(() => root())).toEqual({ status: MOVED, location: '/seats' });
  });

  it('sends the old profiles list to the seats list', () => {
    expect(redirectOf(() => profiles({ url: new URL('http://localhost/profiles') } as never))).toEqual({
      status: MOVED,
      location: '/seats'
    });
  });

  it('sends `?open=` to the seat it named, which is what the old links meant', () => {
    expect(
      redirectOf(() => profiles({ url: new URL('http://localhost/profiles?open=heathcote') } as never))
    ).toEqual({ status: MOVED, location: '/seats/heathcote' });
  });

  it('sends an old profile URL to the seat of that name', () => {
    expect(redirectOf(() => seatRedirect({ params: { name: 'heathcote' } } as never))).toEqual({
      status: MOVED,
      location: '/seats/heathcote'
    });
  });

  it('sends the chains and rankings lists to the seats list', () => {
    expect(redirectOf(() => chains())).toEqual({ status: MOVED, location: '/seats' });
    expect(redirectOf(() => rankings())).toEqual({ status: MOVED, location: '/seats' });
  });

  it('sends a chain or a ranking of one profile to that seat', () => {
    const event = { params: { profile: 'heathcote' } } as never;
    expect(redirectOf(() => chainOf(event))).toEqual({
      status: MOVED,
      location: '/seats/heathcote'
    });
    expect(redirectOf(() => rankingOf(event))).toEqual({
      status: MOVED,
      location: '/seats/heathcote'
    });
  });
});

describe('a seat is one page under its own name', () => {
  it('hands the name in the URL to the page', () => {
    const data = seat({ params: { name: 'heathcote' } } as never) as { name: string };
    expect(data.name).toBe('heathcote');
  });
});
