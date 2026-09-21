// The profiles list is gone: it was the same one-object-per-seat list the seats
// screen now shows, and a seat is a page of its own (`/seats/[name]`).
//
// The URL stays, because it is in people's bookmarks and in the guide's own
// link to it. It lands on the seats list, exactly as `/rankings` and `/chains`
// have since the console took the list over -- and `?open=<name>`, which is how
// the old list linked to one profile's panel (§1.3), lands on that seat.
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

// Not prerendered, and it has to not be: the redirect reads `?open=`, and
// SvelteKit refuses `url.searchParams` while prerendering. A static page would
// also be one file for every query string, so `/profiles?open=x` would lose the
// name on the way. The app is a single-page app with a fallback, so this route
// resolves on the client like every other dynamic one.
export const prerender = false;

export const load: PageLoad = ({ url }) => {
  const open = url.searchParams.get('open');
  redirect(307, open ? `/seats/${encodeURIComponent(open)}` : '/seats');
};
