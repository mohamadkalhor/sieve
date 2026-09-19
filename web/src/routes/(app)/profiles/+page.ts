// The list moved to `/seats` (CONSOLE.md section 6.1): a profile is the stored
// half of a seat now, so the seats list is where you start and the sections in
// the top bar stop naming it. The URL stays because it is in people's
// bookmarks and in the decision log.
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

// Not prerendered, and it has to not be: the redirect reads `?open=`, and
// SvelteKit refuses `url.searchParams` while prerendering. A static page would
// also be one file for every query string, so `/profiles?open=x` would lose the
// name on the way. The app is a single-page app with a fallback, so this route
// resolves on the client like every other dynamic one.
export const prerender = false;

export const load: PageLoad = ({ url }) => {
  // `/profiles?open=x` was the old list with x's panel open (§1.3): the same
  // address means that seat now, and a bare `/profiles` means the list.
  const open = url.searchParams.get('open');
  redirect(307, open ? `/seats/${encodeURIComponent(open)}` : '/seats');
};
