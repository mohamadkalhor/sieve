// A route with a parameter has no build-time list of values to crawl, so it is
// not prerendered. The SPA fallback in `index.html` serves it: FastAPI answers
// any deep link with the shell, and the page reads its own data from `/v1`.
//
// The parent layout prerenders every route that *does* have a fixed path, which
// is where the paint-time win is -- those are the screens someone lands on.
export const prerender = false;
