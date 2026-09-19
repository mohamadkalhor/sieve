// A seat, on its own page.
//
// A seat has more in it than a row can hold without becoming a scroll -- every
// axis, what carried each score, the history, the experience, the cost
// overrides -- so the list is a list and the seat is a page, with the list
// still beside it.
//
// A route with a parameter has no build-time list of values to crawl, so it is
// not prerendered; the SPA fallback in `index.html` serves it.
import type { PageLoad } from './$types';

export const prerender = false;

export const load: PageLoad = ({ params }) => ({ name: params.name });
