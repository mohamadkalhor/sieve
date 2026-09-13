// The per-profile editor is now a row on the Profiles list, opened in place.
// This URL keeps working and opens that row.
//
// A route with a parameter has no build-time list of values to crawl, so it is
// not prerendered; the SPA fallback in `index.html` serves it.
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = ({ params }) => {
  redirect(307, `/profiles?open=${encodeURIComponent(params.name)}`);
};
