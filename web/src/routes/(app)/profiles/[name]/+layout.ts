// `/profiles/<name>` was the seat's page under its old name; it is
// `/seats/<name>` now (CONSOLE.md section 6.1).
//
// The redirect sits in a layout rather than in `+page.ts` so that this route
// stays a redirect and nothing else: a URL in a bookmark keeps landing on the
// seat of that name, and the seat itself is one page under its own name.
import { redirect } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

export const prerender = false;

export const load: LayoutLoad = ({ params }) => {
  redirect(307, `/seats/${encodeURIComponent(params.name)}`);
};
