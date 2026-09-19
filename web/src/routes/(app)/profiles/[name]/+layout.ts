// `/profiles/<name>` was the seat's page under its old name; it is
// `/seats/<name>` now (CONSOLE.md section 6.1).
//
// The redirect sits in a layout rather than in `+page.ts` on purpose: the old
// page file is still here and still reads `data.name`, so a redirect in
// `+page.ts` would change this route's data type underneath it and break the
// type check. Nothing about the page changes -- the layout's redirect wins
// before the page ever loads -- and when package G deletes the old page the
// two files can collapse back into one `+page.ts`.
import { redirect } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

export const prerender = false;

export const load: LayoutLoad = ({ params }) => {
  redirect(307, `/seats/${encodeURIComponent(params.name)}`);
};
