// Kept for the bookmarks. The profile this URL named is opened on the Profiles
// list, which is where its ranking, its chain and its settings now all are.
//
// A route with a parameter has no build-time list of values to crawl, so it is
// not prerendered; the SPA fallback in `index.html` serves it.
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = ({ params }) => {
  redirect(307, `/profiles?open=${encodeURIComponent(params.profile)}`);
};
